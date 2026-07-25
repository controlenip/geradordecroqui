import streamlit as st
import folium
from folium import plugins
from streamlit_folium import st_folium
import requests
import io
import base64
import os
import tempfile
import pandas as pd
import zipfile
import time

# Importação do GPS
from streamlit_geolocation import streamlit_geolocation

# Importações do Playwright para os prints
from playwright.sync_api import sync_playwright

# === CORREÇÃO DO PLAYWRIGHT (NUVEM E LOCAL) ===
if "USERPROFILE" in os.environ:
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(os.environ["USERPROFILE"], "AppData", "Local", "ms-playwright")
else:
    os.system("playwright install chromium")

# Importações GIS e CAD
import numpy as np
import ezdxf
import geopandas as gpd
from shapely.geometry import Point, LineString

# ==========================================
# FUNÇÕES DE CÁLCULO E ROTA
# ==========================================
def obter_rota_ruas(pontos):
    str_coords = ";".join([f"{lon},{lat}" for lat, lon in pontos])
    url = f"http://router.project-osrm.org/route/v1/foot/{str_coords}?overview=full&geometries=geojson"
    try:
        resposta = requests.get(url)
        dados = resposta.json()
        if dados.get("code") == "Ok":
            coordenadas_osrm = dados["routes"][0]["geometry"]["coordinates"]
            return [[coord[1], coord[0]] for coord in coordenadas_osrm]
    except Exception:
        return None
    return None

def calcular_distancia_metros(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    delta_phi = np.radians(lat2 - lat1)
    delta_lambda = np.radians(lon2 - lon1)
    a = np.sin(delta_phi/2.0)**2 + np.cos(phi1) * np.cos(phi2) * np.sin(delta_lambda/2.0)**2
    c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1-a))
    return R * c

def converter_coordenada(valor_str, padrao):
    try:
        if not str(valor_str).strip(): return padrao
        cleaned = str(valor_str).replace(',', '.').strip()
        return float(cleaned)
    except ValueError:
        return padrao

def limpar_dados_formulario():
    campos = [
        'obs_input', 'ccs_input', 'nome_input', 'codigo_input', 'tel_input',
        'lat_c_input', 'lon_c_input', 'num_trafo_input', 'lat_t_input', 'lon_t_input',
        'texto_marcador_input', 'lat_novo_input', 'lon_novo_input'
    ]
    for campo in campos:
        st.session_state[campo] = ""
    st.session_state.print_mapa = None

# ==========================================
# FUNÇÕES DE EXPORTAÇÃO E LOTE
# ==========================================
def gerar_txt_dados(ccs, nome, telefone, codigo, num_trafo, obs, lat_c, lon_c, lat_t, lon_t, marcadores_extras):
    lat_c_str = str(lat_c).replace(',', '.').strip()
    lon_c_str = str(lon_c).replace(',', '.').strip()
    lat_t_str = str(lat_t).replace(',', '.').strip()
    lon_t_str = str(lon_t).replace(',', '.').strip()
    
    texto = f"({ccs} - {nome})\n\n"
    texto += f"TEL: {telefone if str(telefone).strip() else 'Não Informado'}\n"
    texto += f"MD: {codigo if str(codigo).strip() else 'Não Informado'}\n"
    texto += f"TRAFO: {num_trafo if str(num_trafo).strip() else 'Não Informado'}\n"
    
    if marcadores_extras:
        postes_str = [m['texto'] for m in marcadores_extras]
        texto += f"POSTE/ESTRUTURA: {', '.join(postes_str)}\n\n"
    else:
        texto += "POSTE/ESTRUTURA: Não Informado\n\n"
        
    obs_str = obs if str(obs).strip() else "Sem observações."
    texto += f"{obs_str}\n\n"
    
    texto += "CLIENTE:\n"
    texto += f"https://www.google.com.br/maps/place/{lat_c_str},{lon_c_str}\n\n"
    
    texto += "TRAFO:\n"
    texto += f"https://www.google.com.br/maps/place/{lat_t_str},{lon_t_str}\n"
    
    return texto.encode('utf-8')

def gerar_dxf(pontos_rota, coord_trafo, coord_cliente, postes_extras):
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    if pontos_rota and len(pontos_rota) > 1:
        pontos_cad = [(p[1], p[0]) for p in pontos_rota]
        msp.add_lwpolyline(pontos_cad, dxfattribs={'color': 3}) 
    msp.add_point((coord_trafo[1], coord_trafo[0]), dxfattribs={'color': 1}) 
    msp.add_text("TRAFO", dxfattribs={'height': 0.0001, 'color': 1}).set_placement((coord_trafo[1], coord_trafo[0]))
    msp.add_point((coord_cliente[1], coord_cliente[0]), dxfattribs={'color': 4}) 
    msp.add_text("CLIENTE", dxfattribs={'height': 0.0001, 'color': 4}).set_placement((coord_cliente[1], coord_cliente[0]))
    for marc in postes_extras:
        p_lon, p_lat = marc['coord'][1], marc['coord'][0]
        msp.add_point((p_lon, p_lat), dxfattribs={'color': 2}) 
        msp.add_text(marc['texto'], dxfattribs={'height': 0.0001, 'color': 2}).set_placement((p_lon, p_lat))
    buffer = io.StringIO()
    doc.write(buffer)
    return buffer.getvalue()

def gerar_geojson(pontos_rota, coord_trafo, coord_cliente, postes_extras):
    features = []
    features.append({"geometry": Point(coord_trafo[1], coord_trafo[0]), "properties": {"Elemento": "Trafo"}})
    features.append({"geometry": Point(coord_cliente[1], coord_cliente[0]), "properties": {"Elemento": "Cliente"}})
    for p in postes_extras:
        features.append({"geometry": Point(p['coord'][1], p['coord'][0]), "properties": {"Elemento": "Poste", "Rotulo": p['texto']}})
    if pontos_rota and len(pontos_rota) > 1:
        linha = LineString([(p[1], p[0]) for p in pontos_rota])
        features.append({"geometry": linha, "properties": {"Elemento": "Rede de Distribuicao"}})
    gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
    return gdf.to_json()

def gerar_print_mapa(mapa_html_content):
    with tempfile.NamedTemporaryFile(delete=False, suffix='.html', mode='w', encoding='utf-8') as f:
        f.write(mapa_html_content)
        tmp_path = os.path.abspath(f.name)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
            page = browser.new_page(viewport={"width": 1920, "height": 1080})
            file_url = f"file:///{tmp_path.replace(chr(92), '/')}" 
            page.goto(file_url, wait_until="networkidle")
            page.wait_for_timeout(7000)
            map_element = page.locator('.leaflet-container')
            if map_element.count() > 0:
                page.evaluate("window.dispatchEvent(new Event('resize'));")
                page.wait_for_timeout(2000)
                screenshot_bytes = map_element.screenshot()
            else:
                screenshot_bytes = page.screenshot(full_page=True)
            browser.close()
    finally:
        os.remove(tmp_path)
    return screenshot_bytes

def construir_mapa_lote_offline(lat_c, lon_c, lat_t, lon_t, nome, codigo, num_trafo, obs, font_size=8, font_style="Arial", postes_extras=None):
    fl_lat_c = converter_coordenada(lat_c, -4.512536)
    fl_lon_c = converter_coordenada(lon_c, -44.469452)
    fl_lat_t = converter_coordenada(lat_t, -4.513000)
    fl_lon_t = converter_coordenada(lon_t, -44.470000)
    
    centro_mapa = [(fl_lat_c + fl_lat_t) / 2, (fl_lon_c + fl_lon_t) / 2]
    
    m = folium.Map(location=centro_mapa, zoom_start=17)
    folium.TileLayer('CartoDB positron', name='Visão Normal').add_to(m)
    folium.TileLayer('https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', attr='Google', name='Visão Satélite').add_to(m)
    
    pontos_base = [[fl_lat_t, fl_lon_t], [fl_lat_c, fl_lon_c]]
    rota = obter_rota_ruas(pontos_base)
    caminho_final = rota if rota else pontos_base
    
    folium.PolyLine(caminho_final, color="#000", weight=10, opacity=1).add_to(m)
    folium.PolyLine(caminho_final, color="#FFFF00", weight=6, opacity=1).add_to(m)
    
    d_lat = fl_lat_c - fl_lat_t
    d_lon = fl_lon_c - fl_lon_t
    
    off_lat_c = 0.00015 if d_lat >= 0 else -0.00015
    off_lon_c = 0.00025 if d_lon >= 0 else -0.00025
    off_lat_t = -0.00015 if d_lat >= 0 else 0.00015
    off_lon_t = -0.00025 if d_lon >= 0 else 0.00025

    lat_obs = max(fl_lat_c, fl_lat_t) + 0.0006
    lon_obs = (fl_lon_c + fl_lon_t) / 2

    lbl_lat_c = fl_lat_c + off_lat_c
    lbl_lon_c = fl_lon_c + off_lon_c
    lbl_lat_t = fl_lat_t + off_lat_t
    lbl_lon_t = fl_lon_t + off_lon_t

    limites_camera = [
        [fl_lat_c + 0.001, fl_lon_c + 0.001], [fl_lat_c - 0.001, fl_lon_c - 0.001],
        [fl_lat_t + 0.001, fl_lon_t + 0.001], [fl_lat_t - 0.001, fl_lon_t - 0.001],
        [lat_obs + 0.0005, lon_obs + 0.001]
    ]

    card_style = f'background-color: black; color: yellow; border: 2px solid red; padding: 6px; font-size: {font_size}pt; font-family: {font_style}; font-weight: bold; text-align: center; white-space: nowrap; box-shadow: 3px 3px 6px rgba(0,0,0,0.6);'

    folium.Marker([fl_lat_c, fl_lon_c], icon=folium.features.DivIcon(icon_size=(30,30), icon_anchor=(15,15), html='<div style="font-size: 24px; line-height: 1; display: block; filter: drop-shadow(2px 2px 2px white); font-family: Segoe UI Emoji, Apple Color Emoji, Noto Color Emoji, sans-serif;">🏠</div>')).add_to(m)
    html_cliente = f'<div style="{card_style}">{nome}<br>MD: {codigo}</div>'
    folium.Marker([lbl_lat_c, lbl_lon_c], icon=folium.features.DivIcon(icon_size=(250, 60), icon_anchor=(125, 30), html=html_cliente)).add_to(m)
    
    folium.Marker([fl_lat_t, fl_lon_t], icon=folium.features.DivIcon(icon_size=(25,25), icon_anchor=(12,12), html='<div style="font-size: 18px; line-height: 1; display: block; background-color: #b0b0b0; border: 2px solid black; text-align: center; border-radius: 2px; filter: drop-shadow(1px 1px 1px white); font-family: Segoe UI Emoji, Apple Color Emoji, Noto Color Emoji, sans-serif;">⚡</div>')).add_to(m)
    html_trafo = f'<div style="{card_style}">TRAFO: {num_trafo}</div>'
    folium.Marker([lbl_lat_t, lbl_lon_t], icon=folium.features.DivIcon(icon_size=(200, 40), icon_anchor=(100, 20), html=html_trafo)).add_to(m)
    
    if postes_extras:
        for marc in postes_extras:
            m_lat, m_lon = marc["coord"]
            folium.Marker([m_lat, m_lon], icon=folium.features.DivIcon(icon_size=(22,22), icon_anchor=(11,11), html='<div style="font-size: 13px; background-color: #a9a9a9; color: black; border: 2px solid black; border-radius: 50%; width: 22px; height: 22px; line-height: 18px; text-align: center; font-weight: bold; box-shadow: 2px 2px 4px rgba(0,0,0,0.5);">P</div>')).add_to(m)
            html_fixado = f'<div style="{card_style}">{marc["texto"]}</div>'
            folium.Marker([m_lat + 0.00010, m_lon], icon=folium.features.DivIcon(icon_size=(150, 40), icon_anchor=(75, 40), html=html_fixado)).add_to(m)

    if str(obs).strip() and str(obs).lower() != 'nan':
        observacoes_html = str(obs).replace('\n', '<br>')
        html_obs = f'<div style="{card_style}">{observacoes_html}</div>'
        folium.Marker([lat_obs, lon_obs], icon=folium.features.DivIcon(icon_size=(450, 100), icon_anchor=(225, 50), html=html_obs)).add_to(m)
        
    m.fit_bounds(limites_camera)
    return m

def construir_mapa_camadas(is_print, lat_c, lon_c, lat_t, lon_t, nome, codigo, telefone, num_trafo, obs, fl_lat_c, fl_lon_c, fl_lat_t, fl_lon_t):
    """Constrói o mapa dinâmico para visualização em tempo real."""
    f_size = st.session_state.font_size_cards
    f_style = st.session_state.font_style_cards
    card_style = f'background-color: black; color: yellow; border: 2px solid red; padding: 6px; font-size: {f_size}pt; font-family: {f_style}; font-weight: bold; text-align: center; white-space: nowrap; box-shadow: 3px 3px 6px rgba(0,0,0,0.6);'

    if is_print and st.session_state.get("map_center"):
        centro_mapa = st.session_state["map_center"]
        zoom_mapa = st.session_state["map_zoom"]
    else:
        centro_mapa = [fl_lat_c, fl_lon_c]
        zoom_mapa = 17

    m = folium.Map(location=centro_mapa, zoom_start=zoom_mapa)
    folium.TileLayer('CartoDB positron', name='Visão Normal').add_to(m)
    folium.TileLayer('https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', attr='Google', name='Visão Satélite').add_to(m)
    folium.LayerControl(position='topright').add_to(m)
    
    if not is_print:
        minimap = plugins.MiniMap(toggle_display=True)
        m.add_child(minimap)
    
    if str(lat_c).strip() and str(lon_c).strip():
        folium.Marker([fl_lat_c, fl_lon_c], icon=folium.features.DivIcon(icon_size=(30,30), icon_anchor=(15,15), html='<div style="font-size: 24px; line-height: 1; display: block; filter: drop-shadow(2px 2px 2px white); font-family: Segoe UI Emoji, Apple Color Emoji, Noto Color Emoji, sans-serif;">🏠</div>')).add_to(m)
        lbl_lat_cliente = fl_lat_c + float(st.session_state.y_cli)
        lbl_lon_cliente = fl_lon_c + float(st.session_state.x_cli)
        info_tel = f"<br>TEL: {telefone}" if str(telefone).strip() else ""
        html_cliente = f'<div style="{card_style}">{nome}<br>MD: {codigo}{info_tel}</div>'
        folium.Marker([lbl_lat_cliente, lbl_lon_cliente], icon=folium.features.DivIcon(icon_size=(250, 60), icon_anchor=(125, 30), html=html_cliente)).add_to(m)
        
    if str(lat_t).strip() and str(lon_t).strip():
        folium.Marker([fl_lat_t, fl_lon_t], icon=folium.features.DivIcon(icon_size=(25,25), icon_anchor=(12,12), html='<div style="font-size: 18px; line-height: 1; display: block; background-color: #b0b0b0; border: 2px solid black; text-align: center; border-radius: 2px; filter: drop-shadow(1px 1px 1px white); font-family: Segoe UI Emoji, Apple Color Emoji, Noto Color Emoji, sans-serif;">⚡</div>')).add_to(m)
        lbl_lat_trafo = fl_lat_t + float(st.session_state.y_tra)
        lbl_lon_trafo = fl_lon_t + float(st.session_state.x_tra)
        html_trafo = f'<div style="{card_style}">TRAFO: {num_trafo}</div>'
        folium.Marker([lbl_lat_trafo, lbl_lon_trafo], icon=folium.features.DivIcon(icon_size=(200, 40), icon_anchor=(100, 20), html=html_trafo)).add_to(m)

    for idx, marc in enumerate(st.session_state.marcadores_extras):
        m_lat, m_lon = marc["coord"]
        folium.Marker([m_lat, m_lon], icon=folium.features.DivIcon(icon_size=(22,22), icon_anchor=(11,11), html='<div style="font-size: 13px; background-color: #a9a9a9; color: black; border: 2px solid black; border-radius: 50%; width: 22px; height: 22px; line-height: 18px; text-align: center; font-weight: bold; box-shadow: 2px 2px 4px rgba(0,0,0,0.5);">P</div>')).add_to(m)
        html_fixado = f'<div style="{card_style}">{marc["texto"]}</div>'
        folium.Marker([m_lat + 0.00010, m_lon], icon=folium.features.DivIcon(icon_size=(150, 40), icon_anchor=(75, 40), html=html_fixado)).add_to(m)

    if st.session_state.pontos_rota_atual:
        dash = '10, 10' if st.session_state.modo_edicao_rota else None
        folium.PolyLine(st.session_state.pontos_rota_atual, color="#000", weight=10, opacity=1).add_to(m)
        folium.PolyLine(st.session_state.pontos_rota_atual, color="#FFFF00", weight=6, dash_array=dash, opacity=1).add_to(m)

    if str(obs).strip() and str(obs).lower() != 'nan':
        observacoes_html = str(obs).replace('\n', '<br>')
        lbl_lat_obs = max(fl_lat_c, fl_lat_t) + float(st.session_state.y_obs)
        lbl_lon_obs = ((fl_lon_c + fl_lon_t) / 2) + float(st.session_state.x_obs)
        html_obs = f'<div style="{card_style}">{observacoes_html}</div>'
        folium.Marker([lbl_lat_obs, lbl_lon_obs], icon=folium.features.DivIcon(icon_size=(450, 100), icon_anchor=(225, 50), html=html_obs)).add_to(m)
        
    return m

# ==========================================
# CÁPSULA DA INTERFACE VISUAL
# ==========================================
def view_gerador_croqui():
    st.markdown("""
        <style>
        div[data-testid="column"]:nth-of-type(1) { position: -webkit-sticky; position: sticky; top: 2rem; align-self: flex-start; z-index: 999; }
        .stTabs [data-baseweb="tab-list"] { gap: 8px; border-bottom: 1px solid #333; }
        .stTabs [data-baseweb="tab"] { font-weight: 600; background-color: #2b2d35; border-radius: 6px 6px 0px 0px; padding: 12px 24px; border: 1px solid transparent; transition: background-color 0.2s ease;}
        .stTabs [aria-selected="true"] { background-color: #007bff !important; color: white !important; }
        .stTextInput input, .stTextArea textarea { background-color: #12141a !important; border: 1px solid #3a3f4b !important; color: #e0e0e0 !important; border-radius: 6px;}
        .stButton button, .stDownloadButton button { width: 100%; font-weight: bold; border-radius: 6px; transition: all 0.2s ease-in-out; text-transform: uppercase; font-size: 0.9rem;}
        .btn-verde button { background-color: #28a745 !important; color: white !important; }
        .btn-roxo button { background-color: #6f42c1 !important; color: white !important; }
        .btn-laranja button { background-color: #e67e22 !important; color: white !important; }
        .btn-vermelho button { background-color: #dc3545 !important; color: white !important; }
        .btn-azul button { background-color: #007bff !important; color: white !important; }
        .btn-cinza button { background-color: #4a5059 !important; color: white !important; }
        </style>
    """, unsafe_allow_html=True)

    # Inicialização de Estados apenas quando a aba for aberta
    if "y_cli" not in st.session_state: st.session_state.y_cli = 0.00015
    if "x_cli" not in st.session_state: st.session_state.x_cli = 0.00025
    if "y_tra" not in st.session_state: st.session_state.y_tra = -0.00015
    if "x_tra" not in st.session_state: st.session_state.x_tra = -0.00025
    if "y_obs" not in st.session_state: st.session_state.y_obs = 0.0006
    if "x_obs" not in st.session_state: st.session_state.x_obs = 0.0000

    if "font_size_cards" not in st.session_state: st.session_state.font_size_cards = 8
    if "font_style_cards" not in st.session_state: st.session_state.font_style_cards = "Arial"

    if 'marcadores_extras' not in st.session_state: st.session_state.marcadores_extras = []
    if 'pontos_rota_atual' not in st.session_state: st.session_state.pontos_rota_atual = []
    if 'modo_edicao_rota' not in st.session_state: st.session_state.modo_edicao_rota = False
    if 'ultimo_clique' not in st.session_state: st.session_state.ultimo_clique = None
    if 'historico_edicao' not in st.session_state: st.session_state.historico_edicao = []
    if 'print_mapa' not in st.session_state: st.session_state.print_mapa = None

    coluna_mapa, coluna_painel = st.columns([3, 1.2])

    with coluna_painel:
        st.markdown("### ⚙️ NIP - GERADOR DE CROQUIS")
        aba_dados, aba_campo, aba_lote, aba_exportar = st.tabs(["🏢 DADOS", "📍 CAMPO", "📥 LOTE", "💾 EXPORTAR"])
        
        with aba_dados:
            with st.container(border=True):
                st.markdown("##### 🎨 Personalização de Fonte")
                cf1, cf2 = st.columns(2)
                with cf1: st.number_input("Tamanho da Fonte (pt)", 5, 20, step=1, key="font_size_cards")
                with cf2: st.selectbox("Estilo da Fonte", ["Arial", "Verdana", "Tahoma", "Courier New", "Georgia"], key="font_style_cards")

            with st.container(border=True):
                st.markdown("##### 🏠 Dados do Cliente")
                ccs = st.text_input("Solicitação CCS", value="", key="ccs_input")
                c1, c2 = st.columns(2)
                with c1: nome = st.text_input("Nome", value="", key="nome_input")
                with c2: codigo = st.text_input("Código (MD)", value="", key="codigo_input")
                telefone = st.text_input("Telefone", value="", key="tel_input")
                c3, c4 = st.columns(2)
                with c3: lat_c = st.text_input("Latitude Cliente *", value="", key="lat_c_input")
                with c4: lon_c = st.text_input("Longitude Cliente *", value="", key="lon_c_input")
                with st.expander("⚙️ Ajuste Fino do Rótulo (Cliente)"):
                    cc1, cc2 = st.columns(2)
                    with cc1: st.slider("↕ Vertical", -0.0100, 0.0100, step=0.0001, format="%.4f", key="y_cli")
                    with cc2: st.slider("↔ Horizontal", -0.0100, 0.0100, step=0.0001, format="%.4f", key="x_cli")
                
            with st.container(border=True):
                st.markdown("##### ⚡ Transformador (Trafo)")
                num_trafo = st.text_input("Número do Transformador", value="", key="num_trafo_input")
                t1, t2 = st.columns(2)
                with t1: lat_t = st.text_input("Lat Trafo *", value="", key="lat_t_input")
                with t2: lon_t = st.text_input("Lon Trafo *", value="", key="lon_t_input")
                with st.expander("⚙️ Ajuste Fino do Rótulo (Trafo)"):
                    ct1, ct2 = st.columns(2)
                    with ct1: st.slider("↕ Vertical", -0.0100, 0.0100, step=0.0001, format="%.4f", key="y_tra")
                    with ct2: st.slider("↔ Horizontal", -0.0100, 0.0100, step=0.0001, format="%.4f", key="x_tra")

            with st.container(border=True):
                st.markdown("##### 📝 Observações do Projeto")
                obs = st.text_area("Anotações Gerais", value="", key="obs_input", label_visibility="collapsed")
                with st.expander("⚙️ Ajuste Fino do Balão (Observações)"):
                    co1, co2 = st.columns(2)
                    with co1: st.slider("↕ Vertical", -0.0100, 0.0100, step=0.0001, format="%.4f", key="y_obs")
                    with co2: st.slider("↔ Horizontal", -0.0100, 0.0100, step=0.0001, format="%.4f", key="x_obs")

        fl_lat_c = converter_coordenada(lat_c, -4.512536)
        fl_lon_c = converter_coordenada(lon_c, -44.469452)
        fl_lat_t = converter_coordenada(lat_t, -4.513000)
        fl_lon_t = converter_coordenada(lon_t, -44.470000)
        m_lat_default, m_lon_default = (fl_lat_c + fl_lat_t) / 2, (fl_lon_c + fl_lon_t) / 2

        with aba_campo:
            with st.container(border=True):
                st.markdown("##### 📍 Adicionar Poste / Estrutura")
                texto_marcador = st.text_input("Rótulo do Marcador", value=f"POSTE {len(st.session_state.marcadores_extras) + 1}", key="texto_marcador_input")
                loc_gps = streamlit_geolocation()
                lat_val_ini = str(loc_gps['latitude']) if loc_gps and loc_gps.get('latitude') else ""
                lon_val_ini = str(loc_gps['longitude']) if loc_gps and loc_gps.get('longitude') else ""
                pm1, pm2 = st.columns(2)
                with pm1: lat_novo = st.text_input("Lat Poste", value=lat_val_ini, key="lat_novo_input")
                with pm2: lon_novo = st.text_input("Lon Poste", value=lon_val_ini, key="lon_novo_input")
                st.markdown('<div class="btn-roxo">', unsafe_allow_html=True)
                tem_poste_ao_vivo = str(lat_novo).strip() != "" and str(lon_novo).strip() != ""
                if st.button("📍 FIXAR ESTE POSTE", use_container_width=True):
                    if tem_poste_ao_vivo:
                        st.session_state.marcadores_extras.append({"coord": [converter_coordenada(lat_novo, m_lat_default), converter_coordenada(lon_novo, m_lon_default)], "texto": texto_marcador})
                st.markdown('</div>', unsafe_allow_html=True)
            with st.container(border=True):
                st.markdown("##### 🛣️ Ferramentas de Rede")
                col_rt1, col_rt2 = st.columns(2)
                with col_rt1:
                    st.markdown('<div class="btn-verde">', unsafe_allow_html=True)
                    if st.button("🗺️ GERAR ROTA", use_container_width=True):
                        st.session_state.pontos_rota_atual = obter_rota_ruas([[fl_lat_t, fl_lon_t], [fl_lat_c, fl_lon_c]]) or [[fl_lat_t, fl_lon_t], [fl_lat_c, fl_lon_c]]
                    st.markdown('</div>', unsafe_allow_html=True)
                with col_rt2:
                    st.markdown('<div class="btn-vermelho">', unsafe_allow_html=True)
                    if st.button("🗑️ LIMPAR MAPA", use_container_width=True):
                        st.session_state.pontos_rota_atual = []
                        st.session_state.marcadores_extras = []
                    st.markdown('</div>', unsafe_allow_html=True)

        with aba_lote:
            with st.container(border=True):
                st.markdown("##### 🎨 Personalização de Fonte (Lote)")
                clf1, clf2 = st.columns(2)
                with clf1: f_size_lote = st.number_input("Tamanho da Fonte (pt)", 5, 20, value=8, step=1, key="f_size_lote")
                with clf2: f_style_lote = st.selectbox("Estilo da Fonte", ["Arial", "Verdana", "Tahoma", "Courier New", "Georgia"], key="f_style_lote")
            st.markdown("---")
            st.markdown("##### 📥 Modelo de Importação")
            modelo_b64 = "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CwQwaImKCPOiJenYQZD/voeU5SRNd6GnLVo7ss68HB6euTweIT39+aEsRh+zps3ramqZE8caZdWqXufVh6n129VyHFujtkurdVrUVTa1PmWt9fPZP/9xel83tzd1fTtSgaqdWtuu201su11tszJtT+pdVumdTd2UaaeXzQe73TVZum63WdaVhe06TmiXaV5Z+wqT5pga9WaTr7J5vbors6rbF2myIu3U/Xab79pDtXJ1TLkybW7vduNVXe5U4iYv8u7TUNQalavJ8w9V3aQ3hW77wQSjh0b/hvrPOPriHq6kt364VJmvmrqtN92JStv7Tv9w/8axjfnOgocfPTiukm832ce8f4ZPvWrCv9ir8KlW+LWYcf52NaNoDVmZyLy/WC146ptrnZ1u8iJ7u4/uKN3tXqVl/6QKa1SkbbdY5122nlqRXtb32dcf6K6au93sLi/0rucYz1j22VOcXzd6oWd/XnRZU6VddlFXnaL22PW/G6uh9sW2VohHl9l/7vIm09hRhHQ7+pquJulN+zrttqO7pphaF5P3v7W6w/fdti7T9/P6vipqjaH334Qv/THpIH7pqr97W3e879X+z3+8e3WumRwi9rprRvrz8/kL2fwm/SjT9WjXj2PyuVw13nW1aibm+rMzc/1klnjjC3MRj31n7o/PwyAaOwvPREtv5s8vLr7oZppwsqrTu277+Dz70lPL18P74a2X6cPhHeNM7vL11258dh7/Gfff//Dl8N6X/ob7mettnt23X598/3L08C6v1vX91HLjMNZdfTq8HhtXL++Hd9/l624rSeL4Tz/7Jcs/bNVlE0S9sEtvLvtJaWqFTj9TNG7f06n1XQ/n+x4u9c+4//JdD+1vujjMmerq8H1UDTl/XaRVXmxTo/m5n1J7331Fe9Jfpnm+HlJtH1qus01eZet+iKjON68eq10/FFV5cr3M+9jPU3U/bbN+5KzS4s2hvO5rm6/XWb9QWGdPHfjp2fkzM3n26zPv1P6m8p9dZn5+da6m/7pcLH86TnwN5ZoXSHWPyX0m16gGnXGYGjrjXDNrnGvmjXPNzHGuoTvQHof6gw3CDmGLsEdssDiGemRoiAxNkaExMjRHcBi41COXeuRSj1zqkUs9gkPfox551COPeuRRjzzqEZzufOqRTz3yqUc+9cinHsEpPqAeBdSjgHoUUI8C6lHIlv2QehRS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"
            st.download_button(
                label="📄 BAIXAR MODELO DE PLANILHA",
                data=base64.b64decode(modelo_b64),
                file_name="modelo_import_croqui_lote.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
            st.markdown("---")
            arquivo_lote = st.file_uploader("Arraste a Planilha Aqui", type=["csv", "xlsx", "xls"])
            if arquivo_lote:
                try:
                    df_lote = pd.read_csv(arquivo_lote) if arquivo_lote.name.endswith('.csv') else pd.read_excel(arquivo_lote)
                    df_lote.columns = df_lote.columns.str.replace('\n', '').str.strip()
                    if st.button("🚀 GERAR LOTE COMPLETO (ZIP)", use_container_width=True):
                        zip_buffer = io.BytesIO()
                        total = len(df_lote)
                        prog = st.progress(0)
                        with zipfile.ZipFile(zip_buffer, "w") as zf:
                            for idx, row in df_lote.iterrows():
                                ccs_l = str(row.get('Solicitação CCS', idx))
                                mapa_l = construir_mapa_lote_offline(row.get('Latitude Cliente'), row.get('Longitude Cliente'), row.get('Lat Trafo'), row.get('Lon Trafo'), row.get('Nome'), row.get('Código (MD)'), row.get('Número do Transformador'), row.get('Observações do Projeto'), font_size=f_size_lote, font_style=f_style_lote)
                                
                                # Gerar PNG
                                zf.writestr(f"print_rede_{ccs_l}.png", gerar_print_mapa(mapa_l.get_root().render()))
                                
                                # Gerar TXT
                                texto_txt = gerar_txt_dados(
                                    row.get('Solicitação CCS'), row.get('Nome'), row.get('Telefone'), 
                                    row.get('Código (MD)'), row.get('Número do Transformador'), 
                                    row.get('Observações do Projeto'), row.get('Latitude Cliente'), 
                                    row.get('Longitude Cliente'), row.get('Lat Trafo'), row.get('Lon Trafo'), 
                                    []
                                )
                                zf.writestr(f"dados_{ccs_l}.txt", texto_txt)
                                
                                prog.progress((idx + 1) / total)
                        st.session_state.zip_gerado = zip_buffer.getvalue()
                        st.success("Lote gerado!")
                except Exception as e: st.error(f"Erro: {e}")
            if st.session_state.get('zip_gerado'):
                st.download_button("📥 BAIXAR PACOTE (.ZIP)", data=st.session_state.zip_gerado, file_name="lote_croquis.zip", use_container_width=True)

        with aba_exportar:
            if st.button("📸 GERAR CROQUI COMPLETO (PNG + TXT)", use_container_width=True):
                with st.spinner("Gerando..."):
                    mapa_para_print = construir_mapa_camadas(True, lat_c, lon_c, lat_t, lon_t, nome, codigo, telefone, num_trafo, obs, fl_lat_c, fl_lon_c, fl_lat_t, fl_lon_t)
                    print_bytes = gerar_print_mapa(mapa_para_print.get_root().render())
                    txt_bytes = gerar_txt_dados(ccs, nome, telefone, codigo, num_trafo, obs, lat_c, lon_c, lat_t, lon_t, st.session_state.marcadores_extras)
                    
                    zip_manual = io.BytesIO()
                    with zipfile.ZipFile(zip_manual, "w") as zf:
                        zf.writestr(f"print_rede_{ccs}.png", print_bytes)
                        zf.writestr(f"dados_{ccs}.txt", txt_bytes)
                    
                    st.session_state.zip_manual_gerado = zip_manual.getvalue()
                    st.success("Arquivos gerados!")
            
            if st.session_state.get('zip_manual_gerado'):
                st.download_button(label="📥 BAIXAR CROQUI (.ZIP)", data=st.session_state.zip_manual_gerado, file_name=f"croqui_{ccs}.zip", use_container_width=True)

    with coluna_mapa:
        mapa_exibicao = construir_mapa_camadas(False, lat_c, lon_c, lat_t, lon_t, nome, codigo, telefone, num_trafo, obs, fl_lat_c, fl_lon_c, fl_lat_t, fl_lon_t)
        mapa_memoria = st_folium(mapa_exibicao, use_container_width=True, height=1000, key="mapa_principal", returned_objects=["center", "zoom"])
        if mapa_memoria and "center" in mapa_memoria:
            st.session_state.map_center = [mapa_memoria["center"]["lat"], mapa_memoria["center"]["lng"]]
            st.session_state.map_zoom = mapa_memoria["zoom"]
