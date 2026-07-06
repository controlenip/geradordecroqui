import streamlit as st
import folium
from folium import plugins
from streamlit_folium import st_folium
import requests
import io
import base64

# Importação do GPS
from streamlit_geolocation import streamlit_geolocation

# Importações do Playwright para os prints
import os
import tempfile
from playwright.sync_api import sync_playwright

# === CORREÇÃO DO PLAYWRIGHT NO CURSOR ===
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(os.environ["USERPROFILE"], "AppData", "Local", "ms-playwright")

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
# FUNÇÕES DE EXPORTAÇÃO
# ==========================================
def gerar_txt_dados(ccs, nome, tel, md, trafo, obs, lat_c, lon_c, lat_t, lon_t, postes):
    str_postes = "Não Informado"
    if postes:
         str_postes = ", ".join([p['texto'] for p in postes])
         
    telefone_str = tel if str(tel).strip() else "Não Informado"
    
    conteudo = f"{ccs} - {nome}\n\n"
    conteudo += f"TEL: {telefone_str}\n"
    conteudo += f"MD: {md}\n"
    conteudo += f"TRAFO: {trafo}\n"
    conteudo += f"POSTE/ESTRUTURA: {str_postes}\n\n"
    conteudo += f"{obs}\n\n"
    conteudo += f"CLIENTE:\nhttps://www.google.com.br/maps/place/{lat_c},{lon_c}\n\n"
    conteudo += f"TRAFO:\nhttps://www.google.com.br/maps/place/{lat_t},{lon_t}\n"
    
    return conteudo.encode('utf-8')

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
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1920, "height": 1080})
            file_url = f"file:///{tmp_path.replace(chr(92), '/')}" 
            page.goto(file_url)
            page.wait_for_timeout(2000)
            
            map_element = page.locator('.leaflet-container')
            if map_element.count() > 0:
                screenshot_bytes = map_element.screenshot()
            else:
                screenshot_bytes = page.screenshot(full_page=True)
                
            browser.close()
    finally:
        os.remove(tmp_path)
    return screenshot_bytes

# ==========================================
# CONFIGURAÇÃO DE PÁGINA E CSS
# ==========================================
st.set_page_config(layout="wide", page_title="NIP - GERADOR DE CROQUIS")

st.markdown("""
    <style>
    /* Trava a coluna do mapa no topo e permite que ela deslize suavemente */
    div[data-testid="column"]:nth-of-type(1) {
        position: -webkit-sticky;
        position: sticky;
        top: 2rem; 
        align-self: flex-start;
        z-index: 999;
    }
    
    /* Estilização das Abas */
    .stTabs [data-baseweb="tab-list"] { gap: 10px; }
    .stTabs [data-baseweb="tab"] { font-weight: bold; background-color: #1e1e1e; border-radius: 4px 4px 0px 0px; padding: 10px 20px; }
    .stTabs [aria-selected="true"] { background-color: #007bff; color: white !important; }
    
    .stButton button, .stDownloadButton button { width: 100%; font-weight: bold; border-radius: 4px; border: 1px solid white; margin-bottom: 5px; }
    .stTextArea textarea { height: 90px !important; }
    .btn-verde button { background-color: #28a745 !important; color: white !important; }
    .btn-roxo button { background-color: #6f42c1 !important; color: white !important; }
    .btn-laranja button { background-color: #d35400 !important; color: white !important; }
    .btn-vermelho button { background-color: #dc3545 !important; color: white !important; }
    .btn-verde-escuro button { background-color: #19692c !important; color: white !important; }
    .btn-azul button { background-color: #007bff !important; color: white !important; }
    .btn-amarelo button { background-color: #ffc107 !important; color: black !important; }
    .btn-cinza button { background-color: #6c757d !important; color: white !important; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# INICIALIZAÇÃO DE ESTADO (SESSION STATE)
# ==========================================
if "y_cli" not in st.session_state: st.session_state.y_cli = 0.0002
if "x_cli" not in st.session_state: st.session_state.x_cli = -0.0010
if "y_tra" not in st.session_state: st.session_state.y_tra = 0.0002
if "x_tra" not in st.session_state: st.session_state.x_tra = -0.0005
if "y_pos" not in st.session_state: st.session_state.y_pos = 0.0002
if "x_pos" not in st.session_state: st.session_state.x_pos = -0.0005
if "y_obs" not in st.session_state: st.session_state.y_obs = 0.0006
if "x_obs" not in st.session_state: st.session_state.x_obs = 0.0000

if 'marcadores_extras' not in st.session_state: st.session_state.marcadores_extras = []
if 'mostrar_analise' not in st.session_state: st.session_state.mostrar_analise = False
if 'pontos_rota_atual' not in st.session_state: st.session_state.pontos_rota_atual = []
if 'modo_edicao_rota' not in st.session_state: st.session_state.modo_edicao_rota = False
if 'ultimo_clique' not in st.session_state: st.session_state.ultimo_clique = None
if 'historico_edicao' not in st.session_state: st.session_state.historico_edicao = []
if 'print_mapa' not in st.session_state: st.session_state.print_mapa = None

mapa_memoria = st.session_state.get("mapa_principal", {})
if mapa_memoria and "center" in mapa_memoria:
    st.session_state.map_center = [mapa_memoria["center"]["lat"], mapa_memoria["center"]["lng"]]
    st.session_state.map_zoom = mapa_memoria["zoom"]

if st.session_state.modo_edicao_rota:
    if mapa_memoria and mapa_memoria.get("last_clicked"):
        clique = mapa_memoria["last_clicked"]
        novo_ponto = [clique["lat"], clique["lng"]]
        
        if st.session_state.ultimo_clique != novo_ponto and len(st.session_state.pontos_rota_atual) > 0:
            caminho = st.session_state.pontos_rota_atual
            d_inicio = calcular_distancia_metros(caminho[0][0], caminho[0][1], novo_ponto[0], novo_ponto[1])
            d_fim = calcular_distancia_metros(caminho[-1][0], caminho[-1][1], novo_ponto[0], novo_ponto[1])
            
            if d_inicio < d_fim:
                caminho.insert(0, novo_ponto)
                st.session_state.historico_edicao.append({'pos': 'inicio'})
            else:
                caminho.append(novo_ponto)
                st.session_state.historico_edicao.append({'pos': 'fim'})
                
            st.session_state.pontos_rota_atual = caminho
            st.session_state.ultimo_clique = novo_ponto

coluna_mapa, coluna_painel = st.columns([3, 1.2])

# ==========================================
# PAINEL DIREITO (MODERNO E SEPARADO EM ABAS)
# ==========================================
with coluna_painel:
    st.markdown("### ⚙️ NIP - GERADOR DE CROQUIS")
    
    aba_dados, aba_campo, aba_exportar = st.tabs(["🏢 DADOS GERAIS", "📍 TRABALHO DE CAMPO", "💾 EXPORTAR"])
    
    # ------------------------------------------
    # ABA 1: DADOS GERAIS DO PROJETO
    # ------------------------------------------
    with aba_dados:
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
            
        st.markdown("<hr style='margin: 10px 0;'>", unsafe_allow_html=True)
        
        st.markdown("##### ⚡ Transformador (Trafo)")
        num_trafo = st.text_input("Número do Transformador", value="", key="num_trafo_input")
        t1, t2 = st.columns(2)
        with t1: lat_t = st.text_input("Lat Trafo *", value="", key="lat_t_input")
        with t2: lon_t = st.text_input("Lon Trafo *", value="", key="lon_t_input")
        
        with st.expander("⚙️ Ajuste Fino do Rótulo (Trafo)"):
            ct1, ct2 = st.columns(2)
            with ct1: st.slider("↕ Vertical", -0.0100, 0.0100, step=0.0001, format="%.4f", key="y_tra")
            with ct2: st.slider("↔ Horizontal", -0.0100, 0.0100, step=0.0001, format="%.4f", key="x_tra")

        st.markdown("<hr style='margin: 10px 0;'>", unsafe_allow_html=True)
        
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

    # ------------------------------------------
    # ABA 2: TRABALHO DE CAMPO (Postes e Rota)
    # ------------------------------------------
    with aba_campo:
        st.markdown("##### 📍 Adicionar Poste / Estrutura")
        texto_marcador = st.text_input("Rótulo do Marcador", value=f"POSTE {len(st.session_state.marcadores_extras) + 1}", key="texto_marcador_input")
        
        st.markdown("<small style='color: #a0a0a0;'>Capturar Localização Automática (GPS):</small>", unsafe_allow_html=True)
        loc_gps = streamlit_geolocation()
        
        lat_val_ini = str(loc_gps['latitude']) if loc_gps and loc_gps.get('latitude') else f"{m_lat_default:.6f}"
        lon_val_ini = str(loc_gps['longitude']) if loc_gps and loc_gps.get('longitude') else f"{m_lon_default:.6f}"

        pm1, pm2 = st.columns(2)
        with pm1: lat_novo = st.text_input("Lat Poste", value=lat_val_ini, key="lat_novo_input")
        with pm2: lon_novo = st.text_input("Lon Poste", value=lon_val_ini, key="lon_novo_input")
        
        st.markdown("<small style='color: #a0a0a0;'>📋 Checklist de Viabilidade (Campo):</small>", unsafe_allow_html=True)
        chk1, chk2, chk3 = st.columns(3)
        with chk1: chk_poda = st.checkbox("🌳 Poda")
        with chk2: chk_rocha = st.checkbox("🪨 Rocha")
        with chk3: chk_calcada = st.checkbox("🔨 Calçada")
        
        with st.expander("📸 Adicionar Foto da Interferência"):
            foto_poste = st.camera_input("Evidência Fotográfica")

        with st.expander("⚙️ Ajuste Fino do Rótulo (Poste)"):
            cp1, cp2 = st.columns(2)
            with cp1: st.slider("↕ Vertical", -0.0100, 0.0100, step=0.0001, format="%.4f", key="y_pos")
            with cp2: st.slider("↔ Horizontal", -0.0100, 0.0100, step=0.0001, format="%.4f", key="x_pos")

        st.markdown('<div class="btn-roxo">', unsafe_allow_html=True)
        tem_poste_ao_vivo = str(lat_novo).strip() != "" and str(lon_novo).strip() != ""
        if st.button("📍 FIXAR ESTE POSTE", use_container_width=True):
            if tem_poste_ao_vivo:
                foto_b64 = ""
                if foto_poste is not None:
                    foto_b64 = base64.b64encode(foto_poste.getvalue()).decode()
                    
                st.session_state.marcadores_extras.append({
                    "coord": [converter_coordenada(lat_novo, m_lat_default), converter_coordenada(lon_novo, m_lon_default)], 
                    "texto": texto_marcador,
                    "off_lat": st.session_state.y_pos,
                    "off_lon": st.session_state.x_pos,
                    "poda": chk_poda,
                    "rocha": chk_rocha,
                    "calcada": chk_calcada,
                    "foto": foto_b64
                })
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown("<hr style='margin: 15px 0;'>", unsafe_allow_html=True)
        st.markdown("##### 🛣️ Ferramentas de Rede")

        pontos_base = [[fl_lat_t, fl_lon_t]]
        for marc in st.session_state.marcadores_extras: pontos_base.append(marc["coord"])
        if tem_poste_ao_vivo: pontos_base.append([converter_coordenada(lat_novo, m_lat_default), converter_coordenada(lon_novo, m_lon_default)])
        pontos_base.append([fl_lat_c, fl_lon_c])

        col_rt1, col_rt2 = st.columns(2)
        with col_rt1:
            st.markdown('<div class="btn-verde">', unsafe_allow_html=True)
            if st.button("🗺️ GERAR ROTA", use_container_width=True):
                rota = obter_rota_ruas(pontos_base)
                st.session_state.pontos_rota_atual = rota if rota else pontos_base
                st.session_state.modo_edicao_rota = False
                st.session_state.historico_edicao = []
            st.markdown('</div>', unsafe_allow_html=True)
        with col_rt2:
            if st.session_state.modo_edicao_rota:
                cb1, cb2 = st.columns(2)
                with cb1:
                    st.markdown('<div class="btn-verde-escuro">', unsafe_allow_html=True)
                    if st.button("✅ CONCLUIR", use_container_width=True):
                        st.session_state.modo_edicao_rota = False
                        st.rerun()
                    st.markdown('</div>', unsafe_allow_html=True)
                with cb2:
                    st.markdown('<div class="btn-vermelho">', unsafe_allow_html=True)
                    if st.button("↩️ DESFAZER", use_container_width=True):
                        if st.session_state.historico_edicao:
                            ultima_acao = st.session_state.historico_edicao.pop()
                            if ultima_acao['pos'] == 'inicio': st.session_state.pontos_rota_atual.pop(0)
                            else: st.session_state.pontos_rota_atual.pop()
                            st.rerun()
                    st.markdown('</div>', unsafe_allow_html=True)
            else:
                st.markdown('<div class="btn-amarelo">', unsafe_allow_html=True)
                if st.button("✏️ EDITAR CAMINHO", use_container_width=True):
                    if not st.session_state.pontos_rota_atual: st.warning("Gere uma rota primeiro.")
                    else:
                        st.session_state.modo_edicao_rota = True
                        st.session_state.historico_edicao = []
                        st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)
                
        col_cl1, col_cl2 = st.columns(2)
        with col_cl1:
            st.markdown('<div class="btn-cinza">', unsafe_allow_html=True)
            st.button("🧹 APAGAR DADOS", on_click=limpar_dados_formulario, use_container_width=True)
            st.markdown('</div>', unsafe_allow_html=True)
        with col_cl2:
            st.markdown('<div class="btn-vermelho">', unsafe_allow_html=True)
            if st.button("🗑️ LIMPAR MAPA", use_container_width=True):
                st.session_state.pontos_rota_atual = []
                st.session_state.modo_edicao_rota = False
                st.session_state.marcadores_extras = []
                st.session_state.historico_edicao = []
                st.session_state.mostrar_analise = False
                st.session_state.print_mapa = None
            st.markdown('</div>', unsafe_allow_html=True)

    # ------------------------------------------
    # ABA 3: EXPORTAÇÃO E NAVEGAÇÃO
    # ------------------------------------------
    with aba_exportar:
        campos_obrigatorios = [lat_c, lon_c, lat_t, lon_t]
        todos_preenchidos = all([str(campo).strip() != "" for campo in campos_obrigatorios])
        
        st.markdown("##### 💾 Exportar Projeto")
        col_exp1, col_exp2 = st.columns(2)
        with col_exp1:
            st.markdown('<div class="btn-azul">', unsafe_allow_html=True)
            st.download_button(label="🌐 MAPA HTML", data="<html></html>", file_name="croqui_rede.html", mime="text/html", disabled=not todos_preenchidos, use_container_width=True)
            st.markdown('</div>', unsafe_allow_html=True)
        with col_exp2:
            dxf_data = gerar_dxf(st.session_state.pontos_rota_atual, [fl_lat_t, fl_lon_t], [fl_lat_c, fl_lon_c], st.session_state.marcadores_extras)
            st.markdown('<div class="btn-roxo">', unsafe_allow_html=True)
            st.download_button(label="📐 CAD (.dxf)", data=dxf_data, file_name=f"projeto.dxf", mime="application/dxf", disabled=not todos_preenchidos, use_container_width=True)
            st.markdown('</div>', unsafe_allow_html=True)
            
        geojson_data = gerar_geojson(st.session_state.pontos_rota_atual, [fl_lat_t, fl_lon_t], [fl_lat_c, fl_lon_c], st.session_state.marcadores_extras)
        st.markdown('<div class="btn-verde">', unsafe_allow_html=True)
        st.download_button(label="🌍 GIS (GeoJSON)", data=geojson_data, file_name=f"rede.geojson", mime="application/geo+json", disabled=not todos_preenchidos, use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)
        
        st.markdown("##### 📸 Print Alta Resolução")
        st.markdown('<div class="btn-laranja">', unsafe_allow_html=True)
        if st.button("📸 GERAR IMAGEM (.png)", use_container_width=True, disabled=not todos_preenchidos):
            with st.spinner("Preparando a câmera do navegador..."):
                try:
                    mapa_para_foto = construir_mapa_camadas(is_print=True)
                    img_bytes = gerar_print_mapa(mapa_para_foto.get_root().render())
                    st.session_state.print_mapa = img_bytes
                    st.success("Imagem gerada! Baixe o print e os dados abaixo.")
                except Exception as e:
                    st.error(f"Erro ao gerar imagem: {e}")
        st.markdown('</div>', unsafe_allow_html=True)
        
        if st.session_state.print_mapa:
            col_print1, col_print2 = st.columns(2)
            
            with col_print1:
                st.markdown('<div class="btn-azul">', unsafe_allow_html=True)
                st.download_button(label="📥 BAIXAR PRINT", data=st.session_state.print_mapa, file_name=f"print_rede.png", mime="image/png", use_container_width=True)
                st.markdown('</div>', unsafe_allow_html=True)
                
            with col_print2:
                txt_data = gerar_txt_dados(ccs, nome, telefone, codigo, num_trafo, obs, fl_lat_c, fl_lon_c, fl_lat_t, fl_lon_t, st.session_state.marcadores_extras)
                st.markdown('<div class="btn-verde">', unsafe_allow_html=True)
                st.download_button(label="📄 BAIXAR DADOS (TXT)", data=txt_data, file_name=f"DADOS_CROQUI.txt", mime="text/plain", use_container_width=True)
                st.markdown('</div>', unsafe_allow_html=True)
            
        st.markdown("<hr style='margin: 15px 0;'>", unsafe_allow_html=True)
        st.markdown("##### 🚗 Roteamento Automático")
        col_nav1, col_nav2 = st.columns(2)
        with col_nav1:
            link_waze = f"https://waze.com/ul?ll={fl_lat_t},{fl_lon_t}&navigate=yes"
            st.markdown(f'<a href="{link_waze}" target="_blank" style="text-decoration: none;"><div class="btn-azul" style="text-align:center; padding:10px; border-radius:4px; margin-bottom:5px;">🚗 WAZE (TRAFO)</div></a>', unsafe_allow_html=True)
        with col_nav2:
            link_maps = f"https://www.google.com/maps/dir/?api=1&destination={fl_lat_c},{fl_lon_c}"
            st.markdown(f'<a href="{link_maps}" target="_blank" style="text-decoration: none;"><div class="btn-verde" style="text-align:center; padding:10px; border-radius:4px; margin-bottom:5px;">🗺️ MAPS (CLIENTE)</div></a>', unsafe_allow_html=True)
            
        st.markdown("##### 👁️ Visão de Rua")
        if mapa_memoria and mapa_memoria.get("last_clicked"):
            lat_clique = mapa_memoria["last_clicked"]["lat"]
            lon_clique = mapa_memoria["last_clicked"]["lng"]
            url_sv = f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={lat_clique},{lon_clique}"
            st.markdown(f'<a href="{url_sv}" target="_blank" style="text-decoration: none;"><div style="background-color: #007bff; color: white; text-align: center; font-weight: bold; border-radius: 4px; border: 1px solid white; padding: 10px; margin-bottom: 5px;">👀 STREET VIEW DO CLIQUE</div></a>', unsafe_allow_html=True)
        else:
            st.info("👆 Clique no mapa para habilitar.")

# ==========================================
# MOTOR DO MAPA (COLUNA ESQUERDA)
# ==========================================
def construir_mapa_camadas(is_print=False):
    if is_print and st.session_state.get("map_center"):
        centro_mapa = st.session_state["map_center"]
        zoom_mapa = st.session_state["map_zoom"]
    else:
        centro_mapa = [fl_lat_c if fl_lat_c != -4.512536 else -4.512536, fl_lon_c if fl_lon_c != -44.469452 else -44.469452]
        zoom_mapa = 17

    m = folium.Map(location=centro_mapa, zoom_start=zoom_mapa)
    folium.TileLayer('CartoDB positron', name='Visão Normal').add_to(m)
    folium.TileLayer('https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', attr='Google', name='Visão Satélite').add_to(m)
    folium.LayerControl(position='topright').add_to(m)
    
    if not is_print:
        minimap = plugins.MiniMap(toggle_display=True, position='bottomright', width=150, height=150, zoom_level_offset=-5)
        m.add_child(minimap)
    
    # 🏠 Cliente
    if str(lat_c).strip() and str(lon_c).strip():
        folium.Marker([fl_lat_c, fl_lon_c], icon=folium.features.DivIcon(icon_size=(30,30), html='<div style="font-size: 24px;">🏠</div>')).add_to(m)
        lbl_lat_cliente = fl_lat_c + float(st.session_state.y_cli)
        lbl_lon_cliente = fl_lon_c + float(st.session_state.x_cli)
        info_tel = f"<br>TEL: {telefone}" if str(telefone).strip() else ""
        html_cliente = f'<div style="background-color: black; color: yellow; border: 1px solid red; padding: 4px; font-size: 8pt; font-weight: bold; text-align: center; white-space: nowrap; box-shadow: 2px 2px 5px rgba(0,0,0,0.5);">{nome}<br>MD: {codigo}{info_tel}</div>'
        folium.Marker([lbl_lat_cliente, lbl_lon_cliente], icon=folium.features.DivIcon(icon_size=(200, 50), icon_anchor=(100, 25), html=html_cliente)).add_to(m)
        
    # ⚡ Trafo
    if str(lat_t).strip() and str(lon_t).strip():
        folium.Marker([fl_lat_t, fl_lon_t], icon=folium.features.DivIcon(icon_size=(25,25), html='<div style="font-size: 16px; background-color: #b0b0b0; border: 2px solid black; text-align: center;">⚡</div>')).add_to(m)
        lbl_lat_trafo = fl_lat_t + float(st.session_state.y_tra)
        lbl_lon_trafo = fl_lon_t + float(st.session_state.x_tra)
        html_trafo = f'<div style="background-color: black; color: yellow; border: 1px solid red; padding: 4px; font-size: 8pt; font-weight: bold; text-align: center; white-space: nowrap; box-shadow: 2px 2px 5px rgba(0,0,0,0.5);">TRAFO: {num_trafo}</div>'
        folium.Marker([lbl_lat_trafo, lbl_lon_trafo], icon=folium.features.DivIcon(icon_size=(150, 36), icon_anchor=(75, 18), html=html_trafo)).add_to(m)

    # Poste atual
    tem_poste_ao_vivo = str(lat_novo).strip() != "" and str(lon_novo).strip() != ""
    if tem_poste_ao_vivo:
        fl_lat_novo = converter_coordenada(lat_novo, m_lat_default)
        fl_lon_novo = converter_coordenada(lon_novo, m_lon_default)
        folium.Marker([fl_lat_novo, fl_lon_novo], icon=folium.features.DivIcon(icon_size=(22,22), html='<div style="font-size: 13px; background-color: #a9a9a9; color: black; border: 2px solid black; border-radius: 50%; width: 22px; height: 22px; line-height: 18px; text-align: center; font-weight: bold; box-shadow: 2px 2px 4px rgba(0,0,0,0.5);">P</div>')).add_to(m)
        lbl_lat_poste = fl_lat_novo + float(st.session_state.y_pos)
        lbl_lon_poste = fl_lon_novo + float(st.session_state.x_pos)
        html_poste = f'<div style="background-color: black; color: yellow; border: 1px solid red; padding: 4px; font-size: 8pt; font-weight: bold; text-align: center; white-space: nowrap; box-shadow: 2px 2px 5px rgba(0,0,0,0.5);">{texto_marcador}</div>'
        folium.Marker([lbl_lat_poste, lbl_lon_poste], icon=folium.features.DivIcon(icon_size=(150, 36), icon_anchor=(75, 18), html=html_poste)).add_to(m)

    # Postes fixados
    for idx, marc in enumerate(st.session_state.marcadores_extras):
        m_lat, m_lon = marc["coord"]
        o_lat = float(marc.get("off_lat", 0.0002))
        o_lon = float(marc.get("off_lon", -0.0005))
        
        marcador_icone = folium.Marker([m_lat, m_lon], icon=folium.features.DivIcon(icon_size=(22,22), html='<div style="font-size: 13px; background-color: #a9a9a9; color: black; border: 2px solid black; border-radius: 50%; width: 22px; height: 22px; line-height: 18px; text-align: center; font-weight: bold; box-shadow: 2px 2px 4px rgba(0,0,0,0.5); cursor: pointer;">P</div>'))
        
        if marc.get('poda') or marc.get('rocha') or marc.get('calcada') or marc.get('foto'):
            html_popup = f"<div style='font-family: Arial; min-width: 150px;'><h4 style='margin:0px 0px 5px 0px;'>{marc['texto']}</h4>"
            if marc.get('poda'): html_popup += "🌳 Necessita Poda<br>"
            if marc.get('rocha'): html_popup += "🪨 Solo Rochoso<br>"
            if marc.get('calcada'): html_popup += "🔨 Quebra de Calçada<br>"
            if marc.get('foto'): html_popup += f"<br><img src='data:image/png;base64,{marc['foto']}' style='width: 100%; border-radius: 5px;'>"
            html_popup += "</div>"
            folium.Popup(html_popup, max_width=300).add_to(marcador_icone)
            
        marcador_icone.add_to(m)
        html_fixado = f'<div style="background-color: black; color: yellow; border: 1px solid red; padding: 4px; font-size: 8pt; font-weight: bold; text-align: center; white-space: nowrap; box-shadow: 2px 2px 5px rgba(0,0,0,0.5);">{marc["texto"]}</div>'
        folium.Marker([m_lat + o_lat, m_lon + o_lon], icon=folium.features.DivIcon(icon_size=(150, 36), icon_anchor=(75, 18), html=html_fixado)).add_to(m)

    # Rota Amarela
    if st.session_state.pontos_rota_atual:
        dash = '10, 10' if st.session_state.modo_edicao_rota else None
        cor_linha = '#FFFF00' 
        folium.PolyLine(st.session_state.pontos_rota_atual, color="#000", weight=10, opacity=1).add_to(m)
        folium.PolyLine(st.session_state.pontos_rota_atual, color=cor_linha, weight=6, dash_array=dash, opacity=1).add_to(m)

    # Observações no Mapa
    if str(obs).strip():
        observacoes_html = obs.replace('\n', '<br>')
        lbl_lat_obs = ((fl_lat_c + fl_lat_t) / 2) + float(st.session_state.y_obs)
        lbl_lon_obs = ((fl_lon_c + fl_lon_t) / 2) + float(st.session_state.x_obs)
        html_obs = f'<div style="background-color: black; color: yellow; border: 2px solid red; padding: 10px; font-size: 10px; font-weight: bold; box-shadow: 2px 2px 5px rgba(0,0,0,0.5);">{observacoes_html}</div>'
        folium.Marker([lbl_lat_obs, lbl_lon_obs], icon=folium.features.DivIcon(icon_size=(350, 100), icon_anchor=(175, 50), html=html_obs)).add_to(m)
        
    return m

with coluna_mapa:
    mapa_exibicao = construir_mapa_camadas(is_print=False)
    st_folium(mapa_exibicao, use_container_width=True, height=1000, key="mapa_principal", returned_objects=["last_clicked", "center", "zoom"])