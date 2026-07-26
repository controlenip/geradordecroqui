import streamlit as st
import pandas as pd
import numpy as np
import folium
from folium.plugins import MarkerCluster, HeatMap
from streamlit_folium import st_folium
import io
import zipfile
import html
import re
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import time
from datetime import datetime
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

# ==========================================
# CONSTANTES DE NEGÓCIO (Fácil Manutenção)
# ==========================================
STATUS_PADRAO = [
    'EM LEVANTAMENTO', '0', 'SEM INFORMAÇÕES', 'SEM INFORMACOES', 
    'CORREÇÃO DE LEVANTAMENTO', 'CORRECAO DE LEVANTAMENTO', 'PRÉ ANÁLISE', 'PRE ANALISE'
]

TIPOS_PRIORITARIOS = ["CCF", "DIF", "MGD", "MTP", "ASC", "SID"]

# ==========================================
# CONFIGURAÇÃO DE SESSÃO WEB E RETENTATIVAS
# ==========================================
def get_retry_session(retries=3, backoff_factor=0.5):
    """Cria uma sessão HTTP que tenta de novo caso a API de Mapa caia ou rejeite."""
    session = requests.Session()
    retry = Retry(
        total=retries, read=retries, connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504)
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

http_session = get_retry_session()

# Configuração da Página (Deve ser a primeira linha executável do app isolado)
st.set_page_config(page_title="Roteirizador Operacional", page_icon="🚙", layout="wide")

# ==========================================
# INJEÇÃO DE CSS GLOBAL & COMPONENTES PREMIUM
# ==========================================
st.markdown("""
<style>
    /* Aumentado o padding-top para evitar corte pelo cabeçalho nativo do Streamlit */
    .block-container { padding-top: 4rem !important; padding-bottom: 2rem !important; }
    .stSelectbox label, .stFileUploader label, .stRadio label, .stNumberInput label, .stMultiSelect label { font-size: 14px !important; font-weight: 600 !important; color: #1A4F7C !important; }

    /* STEPPER DE PROGRESSO */
    .stepper-container {
        display: flex;
        justify-content: space-between;
        margin-top: 0.5rem;
        margin-bottom: 1.5rem;
        padding: 0.75rem 1rem;
        background: rgba(26, 79, 124, 0.05);
        border-radius: 8px;
        border: 1px solid rgba(26, 79, 124, 0.1);
    }
    .step-item {
        font-size: 13px;
        font-weight: 600;
        color: #6c757d;
        display: flex;
        align-items: center;
        gap: 6px;
    }
    .step-item.active {
        color: #0070C0;
    }
    .step-item.done {
        color: #28a745;
    }

    /* CARDS DE MÉTRICAS CUSTOMIZADOS */
    .metric-card {
        background: #ffffff;
        border: 1px solid #e0e0e0;
        border-radius: 10px;
        padding: 16px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.02);
        display: flex;
        align-items: center;
        gap: 14px;
        margin-bottom: 10px;
    }
    .metric-icon {
        font-size: 28px;
        padding: 10px;
        background: rgba(0, 112, 192, 0.1);
        border-radius: 8px;
        display: flex;
        align-items: center;
        justify-content: center;
    }
    .metric-content .metric-title {
        font-size: 12px;
        font-weight: 600;
        color: #6c757d;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .metric-content .metric-value {
        font-size: 20px;
        font-weight: 700;
        color: #1A4F7C;
    }

    @media (prefers-color-scheme: dark) {
        .stepper-container { background: rgba(255, 255, 255, 0.03); border-color: rgba(255, 255, 255, 0.08); }
        .metric-card { background: #1e1e1e; border-color: #333333; }
        .metric-content .metric-value { color: #64B5F6; }
    }
</style>
""", unsafe_allow_html=True)

# ==========================================
# FUNÇÕES DE LIMPEZA E FORMATAÇÃO
# ==========================================
def limpar_roteirizador():
    st.session_state.roteamento_concluido = False
    st.session_state.vrp_status = "IDLE"
    st.session_state.vrp_state = {}
    st.session_state.df_routed = pd.DataFrame()
    st.session_state.bases_records = []
    st.session_state.tipo_periodo = "Dia"
    st.session_state.colunas_exibir = []
    st.session_state.col_prioridade = "TIPO NOTA"
    st.session_state.colunas_originais = []
    st.rerun()

def normalize_cols(cols):
    new_cols = []
    for c in cols:
        c = str(c).strip().upper()
        c = re.sub(r'[ÁÀÂÃÄ]', 'A', c)
        c = re.sub(r'[ÉÈÊË]', 'E', c)
        c = re.sub(r'[ÍÌÎÏ]', 'I', c)
        c = re.sub(r'[ÓÒÔÕÖ]', 'O', c)
        c = re.sub(r'[ÚÙÛÜ]', 'U', c)
        c = re.sub(r'Ç', 'C', c)
        new_cols.append(c)
    return new_cols

def normalizar_municipios(series_mun):
    s = series_mun.astype(str).str.upper()
    s = s.str.replace(r'[ÁÀÂÃÄ]', 'A', regex=True)
    s = s.str.replace(r'[ÉÈÊË]', 'E', regex=True)
    s = s.str.replace(r'[ÍÌÎÏ]', 'I', regex=True)
    s = s.str.replace(r'[ÓÒÔÕÖ]', 'O', regex=True)
    s = s.str.replace(r'[ÚÙÛÜ]', 'U', regex=True)
    s = s.str.replace(r'Ç', 'C', regex=True)
    return s.str.split('-').str[0].str.strip()

def atualizar_status_via_df(df_principal, df_status, coluna_alvo):
    try:
        chave_nome = df_status.columns[0]
        df_status[chave_nome] = df_status[chave_nome].astype(str).str.strip()
        df_status_map = df_status.set_index(chave_nome)[coluna_alvo].to_dict()
        if 'PROTOCOLO' in df_principal.columns:
            df_principal['PROTOCOLO_STR'] = df_principal['PROTOCOLO'].astype(str).str.strip()
            df_principal['STATUS LIST'] = df_principal['PROTOCOLO_STR'].map(df_status_map).fillna(df_principal.get('STATUS LIST', 'SEM INFORMAÇÕES'))
            df_principal = df_principal.drop(columns=['PROTOCOLO_STR'])
            st.success(f"✅ Atualização Rápida: {len(df_status_map)} status lidos da coluna '{coluna_alvo}' aplicados com sucesso!")
        else:
            st.warning("⚠️ Coluna 'PROTOCOLO' não encontrada na base principal.")
    except Exception as e:
        st.error(f"Erro ao aplicar atualização rápida de status: {e}")
    return df_principal

# ==========================================
# FUNÇÕES MATEMÁTICAS E IA (VRP / TSP 2-Opt)
# ==========================================
def haversine_vectorized(lat1, lon1, lat2, lon2):
    R = 6371.0 
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat/2.0)**2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon/2.0)**2
    c = 2 * np.arcsin(np.sqrt(a))
    return R * c

def kmeans_clustering(coords, k, max_iters=100):
    np.random.seed(42)
    unique_coords = np.unique(coords, axis=0)
    if len(unique_coords) < k: k = len(unique_coords)
    indices = np.random.choice(len(unique_coords), k, replace=False)
    centroids = unique_coords[indices]
    labels = np.zeros(len(coords))
    for _ in range(max_iters):
        diff = coords[:, np.newaxis, :] - centroids[np.newaxis, :, :]
        dists = np.linalg.norm(diff, axis=2)
        labels = np.argmin(dists, axis=1)
        new_centroids = np.array([coords[labels == i].mean(axis=0) if np.sum(labels == i) > 0 else centroids[i] for i in range(k)])
        if np.allclose(centroids, new_centroids): break
        centroids = new_centroids
    return labels, centroids

def otimizar_rota_tsp_2opt(lista_obras, start_lat, start_lon):
    if len(lista_obras) <= 2: return lista_obras
    coords = [(start_lat, start_lon)] + [(r['LATITUDE'], r['LONGITUDE']) for r in lista_obras]
    best_route = list(range(1, len(coords)))
    def calc_dist(route):
        d = haversine_vectorized(coords[0][0], coords[0][1], coords[route[0]][0], coords[route[0]][1])
        for i in range(len(route)-1):
            d += haversine_vectorized(coords[route[i]][0], coords[route[i]][1], coords[route[i+1]][0], coords[route[i+1]][1])
        return d
    best_dist = calc_dist(best_route)
    improved = True
    iters = 0
    while improved and iters < 50:
        improved = False
        for i in range(len(best_route) - 1):
            for j in range(i + 1, len(best_route)):
                new_route = best_route[:i] + best_route[i:j+1][::-1] + best_route[j+1:]
                new_dist = calc_dist(new_route)
                if new_dist < best_dist:
                    best_dist = new_dist
                    best_route = new_route
                    improved = True
        iters += 1
    return [lista_obras[i-1] for i in best_route]

@st.cache_data(show_spinner=False)
def obter_coordenadas_municipio_cached(municipio):
    if not municipio or pd.isna(municipio) or str(municipio).strip() == "": return np.nan, np.nan
    try:
        url = f"https://nominatim.openstreetmap.org/search?q={str(municipio).strip()},+Maranhão,+Brasil&format=json&limit=1"
        r = http_session.get(url, headers={"User-Agent": "GeradorRotasOperacional/9.0"}, timeout=5)
        if r.status_code == 200 and len(r.json()) > 0: return float(r.json()[0]['lat']), float(r.json()[0]['lon'])
    except: pass
    return np.nan, np.nan

def obter_rota_ruas(lat1, lon1, lat2, lon2, vel_fallback_kmh=30):
    try:
        url = f"http://router.project-osrm.org/route/v1/driving/{lon1:.6f},{lat1:.6f};{lon2:.6f},{lat2:.6f}?overview=full&geometries=geojson"
        r = http_session.get(url, headers={"User-Agent": "GeradorRotasOperacional/9.0"}, timeout=5)
        if r.status_code == 200 and r.json().get('code') == 'Ok':
            return r.json()['routes'][0]['geometry']['coordinates'], r.json()['routes'][0]['duration']
    except Exception: pass
    dist_km = haversine_vectorized(lat1, lon1, lat2, lon2)
    return [[lon1, lat1], [lon2, lat2]], (dist_km / vel_fallback_kmh) * 3600

def identificar_icone_folium(row, colunas):
    tipo_str = str(row.get('TIPO LIGACAO', '')) + str(row.get('SERVICO', '')) + str(row.get('TIPO NOTA', ''))
    tipo_str = tipo_str.upper()
    if row.get('PROTOCOLO') == 'RETORNO_BASE': return 'home'
    if row.get('PROTOCOLO') == 'PAUSA_ALMOCO': return 'cutlery'
    if 'NOVA' in tipo_str or 'LIGACAO' in tipo_str or 'UNI' in tipo_str or 'UNR' in tipo_str: return 'bolt'
    if 'MANUT' in tipo_str or 'REPARO' in tipo_str: return 'wrench'
    if 'INSP' in tipo_str or 'VISTORIA' in tipo_str: return 'eye-open'
    return 'info-sign'

def gerar_excel_bytes(df, col_prioridade, colunas_originais=None):
    df_export = df.copy()
    if 'PROTOCOLO' in df_export.columns:
        df_export = df_export[~df_export['PROTOCOLO'].isin(['RETORNO_BASE', 'PAUSA_ALMOCO'])]
        
    if 'ROTA_GEOMETRIA' in df_export.columns: 
        df_export = df_export.drop(columns=['ROTA_GEOMETRIA'])
        
    colunas_remover = ['STATUS LIST', 'INICIO AVARIA', 'STATUS ATUAL (LEVANTAMENTO)', 'DESCRICAO']
    for col in colunas_remover:
        if col in df_export.columns:
            df_export = df_export.drop(columns=[col])

    if colunas_originais:
        cols_atuais = df_export.columns.tolist()
        cols_originais_validas = [c for c in colunas_originais if c in cols_atuais]
        cols_novas_geradas = [c for c in cols_atuais if c not in cols_originais]
        df_export = df_export[cols_originais_validas + cols_novas_geradas]
        
    buf_xl = io.BytesIO()
    with pd.ExcelWriter(buf_xl, engine='openpyxl') as writer:
        df_export.to_excel(writer, index=False, sheet_name='Roteiro')
        ws = writer.sheets['Roteiro']
        
        header_fill = PatternFill(start_color='0070C0', end_color='0070C0', fill_type='solid')
        header_font = Font(color='FFFFFF', bold=True)
        center_align = Alignment(horizontal='center', vertical='center')
        left_align = Alignment(horizontal='left', vertical='center')
        
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = center_align
            
        col_types = {}
        for col_idx, col_name in enumerate(df_export.columns, 1):
            col_letter = get_column_letter(col_idx)
            col_name_upper = str(col_name).upper()
            
            if any(x in col_name_upper for x in ['NOME', 'CLIENTE', 'ENDEREÇO', 'ENDERECO', 'INFORMAÇ', 'INFORMAC', 'DESCRIC']):
                ws.column_dimensions[col_letter].width = 45.0
            elif any(x in col_name_upper for x in ['PROTOCOLO', 'MUNICIPIO', 'BASE', 'LOCALIDADE']):
                ws.column_dimensions[col_letter].width = 25.0
            else:
                ws.column_dimensions[col_letter].width = 18.0
                
            if col_name_upper in ['ORDEM', 'SEMANA', 'DIA', 'PERIODO', 'DISTANCIA_PONTO_ANTERIOR_KM', 'DISTANCIA_PROXIMO_PONTO_KM', 'TEMPO_VIAGEM_MINUTOS', 'PRIORIDADE', 'LATITUDE', 'LONGITUDE']:
                col_types[col_idx] = center_align
            else:
                col_types[col_idx] = left_align

        red_font = Font(color="FF0000", bold=True)
        prio_idx = df_export.columns.get_loc('PRIORIDADE') + 1 if 'PRIORIDADE' in df_export.columns else None
        prio_target_idx = df_export.columns.get_loc(col_prioridade) + 1 if col_prioridade in df_export.columns else None

        for row_idx in range(2, len(df_export) + 2):
            for col_idx in range(1, len(df_export.columns) + 1):
                cell = ws.cell(row=row_idx, column=col_idx)
                cell.alignment = col_types.get(col_idx, left_align)
                
            if prio_idx and ws.cell(row=row_idx, column=prio_idx).value == "Sim":
                ws.cell(row=row_idx, column=prio_idx).font = red_font
                if prio_target_idx and col_prioridade != "Nenhuma":
                    try:
                        ws.cell(row=row_idx, column=prio_target_idx).font = red_font
                    except: pass
                    
    return buf_xl.getvalue()

def gerar_kml_agrupado(df_rota, bases_records, doc_name, cols_exibir, lista_todas_bases=None):
    if lista_todas_bases is None:
        lista_todas_bases = df_rota['BASE_ATRIBUIDA'].unique().tolist()
        
    kml = f'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
  <name>{doc_name}</name>
  <Style id="linha-rota-contorno"><LineStyle><color>ff000000</color><width>8</width></LineStyle></Style>
  <Style id="icon-blue">
    <IconStyle><scale>1.1</scale><Icon><href>http://maps.google.com/mapfiles/kml/paddle/blu-blank.png</href></Icon><hotSpot x="32" xunits="pixels" y="64" yunits="insetPixels"/></IconStyle>
    <LabelStyle><scale>0.9</scale></LabelStyle>
  </Style>
  <Style id="icon-red">
    <IconStyle><scale>1.3</scale><Icon><href>http://maps.google.com/mapfiles/kml/paddle/red-blank.png</href></Icon><hotSpot x="32" xunits="pixels" y="64" yunits="insetPixels"/></IconStyle>
    <LabelStyle><scale>1.0</scale></LabelStyle>
  </Style>
  <Style id="icon-green"><IconStyle><scale>1.2</scale><Icon><href>https://maps.google.com/mapfiles/kml/shapes/homegardenbusiness.png</href></Icon></IconStyle></Style>
  <Style id="icon-yellow"><IconStyle><scale>1.3</scale><Icon><href>https://maps.google.com/mapfiles/kml/shapes/dining.png</href></Icon><LabelStyle><scale>1.0</scale></LabelStyle></IconStyle></Style>
'''

    kml_cores = ['ff4b19e6', 'ffd4bc00', 'ffb5513f', 'ff889600', 'ff0098ff', 'ffb0279c', 'ff39dccd', 'ff631ee9', 'ff3bebff', 'ff485579']
    for idx, b_nome in enumerate(lista_todas_bases):
        cor_kml = kml_cores[idx % len(kml_cores)]
        nome_limpo = re.sub(r'[^A-Za-z0-9_]', '', str(b_nome))
        kml += f'  <Style id="rota-centro-{nome_limpo}"><LineStyle><color>{cor_kml}</color><width>5</width></LineStyle></Style>\n'

    for base_nome in df_rota['BASE_ATRIBUIDA'].unique():
        df_base = df_rota[df_rota['BASE_ATRIBUIDA'] == base_nome]
        base_ref = next((b for b in bases_records if b['LEVANTADOR'] == base_nome), None)
        b_lat, b_lon = float(str(base_ref['LATITUDE']).replace(',','.')), float(str(base_ref['LONGITUDE']).replace(',','.'))
        res_nome = str(base_ref.get('RESIDENCIA', base_nome))
        nome_limpo_base = re.sub(r'[^A-Za-z0-9_]', '', str(base_nome))

        kml += f'  <Folder>\n    <name>Levantador: {html.escape(str(base_nome))}</name>\n'
        kml += f'    <Placemark><name>BASE: {html.escape(str(res_nome))}</name><styleUrl>#icon-green</styleUrl><Point><coordinates>{b_lon},{b_lat},0</coordinates></Point></Placemark>\n'

        for semana in df_base['SEMANA'].unique():
            df_semana = df_base[df_base['SEMANA'] == semana]
            kml += f'    <Folder>\n      <name>Semana {semana}</name>\n'

            for dia in df_semana['DIA'].unique():
                df_dia = df_semana[df_semana['DIA'] == dia].copy().sort_values(by='ORDEM')
                kml += f'      <Folder>\n        <name>Dia {dia}</name>\n'

                coords_linha_kml = ""
                for _, row in df_dia.iterrows():
                    lon, lat = str(row['LONGITUDE']).replace(',','.'), str(row['LATITUDE']).replace(',','.')
                    desc_parts = [f"<b>Ordem na Rota:</b> {row.get('ORDEM', 0)}", f"<b>Distância Anterior:</b> {row.get('DISTANCIA_PONTO_ANTERIOR_KM', 0)} KM", f"<b>Tempo Viagem:</b> {row.get('TEMPO_VIAGEM_MINUTOS', 0)} Min"]
                    
                    if row.get('PROTOCOLO') == 'RETORNO_BASE':
                        desc_cdata, nome_ponto, style_url = "<b>RETORNO À BASE DE ORIGEM</b>", "🏠 FIM DO DIA - RETORNO", "#icon-green"
                    elif row.get('PROTOCOLO') == 'PAUSA_ALMOCO':
                        desc_cdata, nome_ponto, style_url = "<b>PAUSA PROGRAMADA PARA REFEIÇÃO (1h)</b>", "🍔 ALMOÇO DA EQUIPE", "#icon-yellow"
                    else:
                        for col in cols_exibir:
                            if col in row: desc_parts.append(f"<b>{col}:</b> {html.escape(str(row[col]))}")
                        desc_cdata = "<br>".join(desc_parts)
                        tag_prio = "[PRIORIDADE] " if row.get('PRIORIDADE') == "Sim" else ""
                        nome_ponto = f"{tag_prio}[{row.get('ORDEM', 0)}] Prot: {html.escape(str(row.get('PROTOCOLO', 'Sem Protocolo')))}"
                        style_url = "#icon-red" if row.get('PRIORIDADE') == "Sim" else "#icon-blue"

                    kml += f'        <Placemark><name>{nome_ponto}</name><description><![CDATA[{desc_cdata}]]></description><styleUrl>{style_url}</styleUrl><Point><coordinates>{lon},{lat},0</coordinates></Point></Placemark>\n'
                    if isinstance(row.get('ROTA_GEOMETRIA'), list):
                        coords_linha_kml += "".join([f"          {pt_lon},{pt_lat},0\n" for pt_lon, pt_lat in row['ROTA_GEOMETRIA']])
                    else:
                        coords_linha_kml += f"          {lon},{lat},0\n"

                kml += f'        <Placemark><name>Contorno Rota</name><styleUrl>#linha-rota-contorno</styleUrl><LineString><tessellate>1</tessellate><coordinates>\n{coords_linha_kml}            </coordinates></LineString></Placemark>\n' 
                kml += f'        <Placemark><name>Traçado Rota</name><styleUrl>#rota-centro-{nome_limpo_base}</styleUrl><LineString><tessellate>1</tessellate><coordinates>\n{coords_linha_kml}            </coordinates></LineString></Placemark>\n      </Folder>\n' 
            kml += '    </Folder>\n' 
        kml += '  </Folder>\n' 
    kml += '</Document>\n</kml>'
    return kml

# ==========================================
# VIEW PRINCIPAL DA PÁGINA
# ==========================================
def view_roteirizador():
    if "roteamento_concluido" not in st.session_state: st.session_state.roteamento_concluido = False
    if "vrp_status" not in st.session_state: st.session_state.vrp_status = "IDLE"
    if "vrp_state" not in st.session_state: st.session_state.vrp_state = {}
    if "df_routed" not in st.session_state: st.session_state.df_routed = pd.DataFrame()
    if "bases_records" not in st.session_state: st.session_state.bases_records = []
    if "colunas_exibir" not in st.session_state: st.session_state.colunas_exibir = []
    if "col_prioridade" not in st.session_state: st.session_state.col_prioridade = "TIPO NOTA"
    if "colunas_originais" not in st.session_state: st.session_state.colunas_originais = []

    # =============================================================
    # STEPPER DE PROGRESSO VISUAL NO TOPO
    # =============================================================
    status_exec = st.session_state.vrp_status
    is_done = st.session_state.roteamento_concluido
    
    s1_class = "step-item done" if (status_exec != "IDLE" or is_done) else "step-item active"
    s2_class = "step-item done" if (status_exec != "IDLE" or is_done) else "step-item active"
    s3_class = "step-item active" if status_exec != "IDLE" else ("step-item done" if is_done else "step-item")
    s4_class = "step-item active" if is_done else "step-item"
    
    st.markdown(f"""
    <div class="stepper-container">
        <div class="{s1_class}">📁 1. Upload de Dados</div>
        <div class="{s2_class}">⚙️ 2. Filtros e Configuração</div>
        <div class="{s3_class}">🚀 3. Roteirização Inteligente</div>
        <div class="{s4_class}">🎯 4. Resultados e Mapas</div>
    </div>
    """, unsafe_allow_html=True)

    # =============================================================
    # SIDEBAR: CONFIGURAÇÕES CONSTANTES E TIMER ANIMADO
    # =============================================================
    is_locked = status_exec != "IDLE" or is_done
    
    with st.sidebar:
        st.markdown("### ⚙️ Gestão de Esforço Diário")
        tipo_periodo = st.radio("Como agrupar o roteiro?", ["Dia", "Semana"], horizontal=True, disabled=is_locked)
        modo_limite = st.radio("Critério limitador da equipe:", ["Quantidade Fixa de Obras", "Carga Horária (Tempo Real via Satélite)"], disabled=is_locked)
        
        limite_km_diario = st.slider(f"Limite Máximo de KM por {tipo_periodo}", min_value=0, max_value=500, value=500, step=5, disabled=is_locked)
        
        obras_por_periodo = 10
        horas_por_dia = 8.0
        tempo_medio_obra = 1.5
        velocidade_media_kmh = 30.0
        
        if modo_limite == "Quantidade Fixa de Obras":
            obras_por_periodo = st.number_input(f"Máximo de Obras por {tipo_periodo}", min_value=1, value=10, step=1, disabled=is_locked)
            limite_periodos = st.number_input(f"Limite total de {tipo_periodo}s a roteirizar", min_value=1, value=5, step=1, disabled=is_locked)
        else:
            horas_por_dia = st.number_input(f"Horas de trabalho disponíveis por {tipo_periodo}", min_value=1.0, value=8.0, step=0.5, disabled=is_locked)
            tempo_medio_obra = st.number_input("Tempo médio de execução por obra (Horas)", min_value=0.1, value=1.5, step=0.1, disabled=is_locked)
            velocidade_media_kmh = st.number_input("Velocidade (Plano B de Conexão) (km/h)", min_value=10.0, value=30.0, step=5.0, disabled=is_locked)
            limite_periodos = st.number_input(f"Limite total de {tipo_periodo}s a roteirizar", min_value=1, value=5, step=1, disabled=is_locked)
            
        st.markdown("---")
        timer_placeholder = st.empty()

    # -------------------------------------------------------------
    # 1. TELA DE RESULTADOS (Após Roteirização Finalizada)
    # -------------------------------------------------------------
    if is_done and not st.session_state.df_routed.empty:
        st.markdown("## 🎯 Resultados da Roteirização Corporativa")
        st.markdown("### ✍️ Ajuste Fino Manual (Painel do Despachante)")
        st.info("Dê um **duplo clique** nas células abaixo para alterar o responsável ou a ordem das obras. Suas edições sairão direto nos downloads finais.")
        
        df_editado_ui = st.data_editor(
            st.session_state.df_routed, use_container_width=True,
            column_config={ "ROTA_GEOMETRIA": None, "LATITUDE": st.column_config.NumberColumn(disabled=True), "LONGITUDE": st.column_config.NumberColumn(disabled=True), "DISTANCIA_PONTO_ANTERIOR_KM": st.column_config.NumberColumn(disabled=True), "DISTANCIA_PROXIMO_PONTO_KM": st.column_config.NumberColumn(disabled=True), "TEMPO_VIAGEM_MINUTOS": st.column_config.NumberColumn(disabled=True) }
        )
        
        df_routed = df_editado_ui.copy()
        bases_records = st.session_state.bases_records
        tipo_periodo = st.session_state.tipo_periodo
        colunas_exibir = st.session_state.colunas_exibir
        col_prioridade = st.session_state.col_prioridade
        colunas_originais = st.session_state.colunas_originais
        
        df_real_tasks = df_routed[~df_routed['PROTOCOLO'].isin(['RETORNO_BASE', 'PAUSA_ALMOCO'])]
        
        # =============================================================
        # CARDS DE MÉTRICAS (DASHBOARD PREMIUM)
        # =============================================================
        tot_obras = len(df_real_tasks)
        tot_equipes = df_routed['BASE_ATRIBUIDA'].nunique()
        tot_km = f"{df_routed['DISTANCIA_PONTO_ANTERIOR_KM'].sum():.1f} km"
        tot_prio = len(df_real_tasks[df_real_tasks['PRIORIDADE'] == 'Sim']) if 'PRIORIDADE' in df_real_tasks else 0

        c_m1, c_m2, c_m3, c_m4 = st.columns(4)
        with c_m1:
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-icon">📌</div>
                <div class="metric-content">
                    <div class="metric-title">Obras Roteirizadas</div>
                    <div class="metric-value">{tot_obras}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)
        with c_m2:
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-icon">👥</div>
                <div class="metric-content">
                    <div class="metric-title">Equipes em Campo</div>
                    <div class="metric-value">{tot_equipes}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)
        with c_m3:
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-icon">🛣️</div>
                <div class="metric-content">
                    <div class="metric-title">KM Total Projetado</div>
                    <div class="metric-value">{tot_km}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)
        with c_m4:
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-icon">🚨</div>
                <div class="metric-content">
                    <div class="metric-title">Prioridades</div>
                    <div class="metric-value">{tot_prio}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("---")
        st.markdown("### 📊 Dashboards de Produtividade")
        c_dash1, c_dash2 = st.columns(2)
        with c_dash1:
            st.markdown("##### 📦 Volume de Obras por Equipe")
            st.bar_chart(df_real_tasks['BASE_ATRIBUIDA'].value_counts(), color="#1A4F7C")
        with c_dash2:
            st.markdown("##### 🛣️ Quilometragem Projetada por Equipe")
            st.bar_chart(df_routed.groupby('BASE_ATRIBUIDA')['DISTANCIA_PONTO_ANTERIOR_KM'].sum(), color="#FF4B4B")
        st.markdown("---")

        st.markdown("#### 🗺️ Visualização Geográfica do Plano")
        mapa = folium.Map(location=[df_routed['LATITUDE'].mean(), df_routed['LONGITUDE'].mean()], zoom_start=8) if not df_routed.empty else folium.Map(location=[-5.2, -45.0], zoom_start=7)
        
        cores_folium = ['#e6194b', '#00bcd4', '#3f51b5', '#009688', '#ff9800', '#9c27b0', '#cddc39', '#e91e63', '#ffeb3b', '#795548']
        lista_bases_mapa = df_routed['BASE_ATRIBUIDA'].unique().tolist()
        
        heat_data = [[r['LATITUDE'], r['LONGITUDE']] for _, r in df_real_tasks.iterrows()]
        HeatMap(heat_data, name="🔥 Mapa de Calor (Demandas)", radius=15, blur=10).add_to(mapa)
        
        marker_cluster = MarkerCluster(name="Obras (Agrupadas)").add_to(mapa)
        
        for base_nome in lista_bases_mapa:
            idx_cor = lista_bases_mapa.index(base_nome)
            cor_rota = cores_folium[idx_cor % len(cores_folium)]
            df_base_rota = df_routed[df_routed['BASE_ATRIBUIDA'] == base_nome]
            base_ref = next((b for b in bases_records if b['LEVANTADOR'] == base_nome), None)
            b_lat, b_lon = float(str(base_ref['LATITUDE']).replace(',','.')), float(str(base_ref['LONGITUDE']).replace(',','.'))
            folium.Marker([b_lat, b_lon], icon=folium.Icon(color='black', icon='home', prefix='fa'), tooltip=f"Base: {base_nome}").add_to(mapa)
            
            for periodo_val in df_base_rota['PERIODO'].unique():
                df_periodo = df_base_rota[df_base_rota['PERIODO'] == periodo_val]
                fg_linhas = folium.FeatureGroup(name=f"Linhas {base_nome} | P: {periodo_val}", show=False)
                
                pontos_linha_folium = []
                for _, r in df_periodo.iterrows():
                    if isinstance(r.get('ROTA_GEOMETRIA'), list):
                        for lon, lat in r['ROTA_GEOMETRIA']: pontos_linha_folium.append([lat, lon]) 
                            
                folium.PolyLine(pontos_linha_folium, color='black', weight=7, opacity=0.9).add_to(fg_linhas)
                folium.PolyLine(pontos_linha_folium, color=cor_rota, weight=3, opacity=1.0).add_to(fg_linhas)
                fg_linhas.add_to(mapa)
                
                for _, r in df_periodo.iterrows():
                    if r['PROTOCOLO'] in ['RETORNO_BASE', 'PAUSA_ALMOCO']: continue
                    icone = identificar_icone_folium(r, df_routed.columns)
                    cor_icone = 'red' if r.get('PRIORIDADE') == "Sim" else 'blue'
                    
                    # =============================================================
                    # POP-UPS DO MAPA (FOLIUM) MAIS ELEGANTES (MINI-CARDS HTML)
                    # =============================================================
                    pop_header_bg = "#d9534f" if r.get('PRIORIDADE') == "Sim" else "#0070C0"
                    pop_prio_txt = "🚨 OBRA PRIORITÁRIA" if r.get('PRIORIDADE') == "Sim" else "📍 Atendimento Padrão"
                    
                    extra_rows = ""
                    for c in colunas_exibir:
                        if c in r: extra_rows += f"<tr><td style='padding:3px 6px; font-weight:bold; color:#555;'>{c}:</td><td style='padding:3px 6px; color:#333;'>{r[c]}</td></tr>"

                    popup_html = f"""
                    <div style="font-family:sans-serif; width:260px; border-radius:8px; overflow:hidden; box-shadow:0 2px 5px rgba(0,0,0,0.15);">
                        <div style="background:{pop_header_bg}; color:white; padding:8px 10px; font-size:13px; font-weight:bold;">
                            {pop_prio_txt}
                        </div>
                        <div style="padding:10px; background:#fafafa; font-size:12px;">
                            <table style="width:100%; border-collapse:collapse;">
                                <tr><td style="padding:3px 6px; font-weight:bold; color:#555;">Protocolo:</td><td style="padding:3px 6px; color:#333;">{r.get('PROTOCOLO', 'N/A')}</td></tr>
                                <tr><td style="padding:3px 6px; font-weight:bold; color:#555;">Ordem:</td><td style="padding:3px 6px; color:#333;">{r.get('ORDEM', 0)} ({tipo_periodo} {r.get('PERIODO', 0)})</td></tr>
                                <tr><td style="padding:3px 6px; font-weight:bold; color:#555;">Distância Ant.:</td><td style="padding:3px 6px; color:#333;">{r.get('DISTANCIA_PONTO_ANTERIOR_KM', 0)} KM</td></tr>
                                <tr><td style="padding:3px 6px; font-weight:bold; color:#555;">Tempo Est.:</td><td style="padding:3px 6px; color:#333;">{r.get('TEMPO_VIAGEM_MINUTOS', 0)} Min</td></tr>
                                {extra_rows}
                            </table>
                        </div>
                    </div>
                    """
                    folium.Marker([r['LATITUDE'], r['LONGITUDE']], icon=folium.Icon(color=cor_icone, icon=icone), popup=folium.Popup(popup_html, max_width=300)).add_to(marker_cluster)
        
        folium.LayerControl().add_to(mapa)
        st_folium(mapa, use_container_width=True, height=550, returned_objects=[])

        st.markdown("#### 📥 Baixar Resultados e Integrações")
        data_atual = datetime.now().strftime("%d_%m_%Y")
        
        buf_zip_xl = io.BytesIO()
        with zipfile.ZipFile(buf_zip_xl, 'w', zipfile.ZIP_DEFLATED) as zip_xl:
            zip_xl.writestr(f"Roteiro_Geral_{data_atual}.xlsx", gerar_excel_bytes(df_routed, col_prioridade, colunas_originais))
            planilhas_geradas = [f"Roteiro_Geral_{data_atual}.xlsx"]

            resumo_levantadores = []
            for base in df_routed['BASE_ATRIBUIDA'].unique():
                df_base = df_routed[df_routed['BASE_ATRIBUIDA'] == base]
                df_base_real = df_base[~df_base['PROTOCOLO'].isin(['RETORNO_BASE', 'PAUSA_ALMOCO'])]
                base_ref = next((b for b in bases_records if b['LEVANTADOR'] == base), None)
                tipo_eq = base_ref.get('TIPO_EQUIPE', 'PRINCIPAL') if base_ref else 'DESCONHECIDO'
                qtd_comum = len(df_base_real[df_base_real['PRIORIDADE'] == 'Não']) if 'PRIORIDADE' in df_base_real.columns else len(df_base_real)
                qtd_prio = len(df_base_real[df_base_real['PRIORIDADE'] == 'Sim']) if 'PRIORIDADE' in df_base_real.columns else 0
                total_km = df_base['DISTANCIA_PONTO_ANTERIOR_KM'].sum()
                
                resumo_levantadores.append({
                    'LEVANTADOR': base,
                    'TIPO EQUIPE': tipo_eq,
                    'OBRAS COMUNS': qtd_comum,
                    'OBRAS PRIORITARIAS': qtd_prio,
                    'TOTAL OBRAS': qtd_comum + qtd_prio,
                    'KM TOTAL PREVISTO': round(total_km, 2)
                })

            buf_resumo_lev = io.BytesIO()
            with pd.ExcelWriter(buf_resumo_lev, engine='openpyxl') as writer:
                df_resumo = pd.DataFrame(resumo_levantadores)
                df_resumo.to_excel(writer, index=False, sheet_name='Resumo')
                ws_resumo = writer.sheets['Resumo']
                
                header_fill = PatternFill(start_color='0070C0', end_color='0070C0', fill_type='solid')
                header_font = Font(color='FFFFFF', bold=True)
                center_align = Alignment(horizontal='center', vertical='center')
                
                for cell in ws_resumo[1]:
                    cell.fill = header_fill
                    cell.font = header_font
                    
                col_widths = {'A': 53.0, 'B': 16.0, 'C': 16.0, 'D': 21.0, 'E': 14.0, 'F': 20.0}
                for col_letter, width in col_widths.items():
                    ws_resumo.column_dimensions[col_letter].width = width
                    
                for row in ws_resumo.iter_rows(min_row=2, min_col=3, max_col=6):
                    for cell in row:
                        cell.alignment = center_align

            zip_xl.writestr(f"Resumo_Levantadores_{data_atual}.xlsx", buf_resumo_lev.getvalue())
            planilhas_geradas.append(f"Resumo_Levantadores_{data_atual}.xlsx")
            
            for base_nome in df_routed['BASE_ATRIBUIDA'].unique():
                df_lev = df_routed[df_routed['BASE_ATRIBUIDA'] == base_nome].copy()
                nome_seguro = re.sub(r'[^A-Za-z0-9_]', '', str(base_nome).replace(" ", "_"))
                if not df_lev.empty:
                    zip_xl.writestr(f"Roteiro_{nome_seguro}_{data_atual}.xlsx", gerar_excel_bytes(df_lev, col_prioridade, colunas_originais))
                    planilhas_geradas.append(f"Roteiro_{nome_seguro}_{data_atual}.xlsx")
                    
        zip_xl_bytes = buf_zip_xl.getvalue()

        buf_zip_kml = io.BytesIO()
        with zipfile.ZipFile(buf_zip_kml, 'w', zipfile.ZIP_DEFLATED) as zip_kml:
            lista_bases_geral = df_routed['BASE_ATRIBUIDA'].unique().tolist()
            zip_kml.writestr(f"Rota_Geral_{data_atual}.kml", gerar_kml_agrupado(df_routed, bases_records, f"Rota_Geral_{data_atual}", colunas_exibir, lista_bases_geral).encode('utf-8'))
            mapas_gerados = [f"Rota_Geral_{data_atual}.kml"]
            
            for base_nome in df_routed['BASE_ATRIBUIDA'].unique():
                df_lev = df_routed[df_routed['BASE_ATRIBUIDA'] == base_nome].copy()
                nome_seguro = re.sub(r'[^A-Za-z0-9_]', '', str(base_nome).replace(" ", "_"))
                if not df_lev.empty:
                    zip_kml.writestr(f"Rota_{nome_seguro}_{data_atual}.kml", gerar_kml_agrupado(df_lev, bases_records, f"Rota_{nome_seguro}", colunas_exibir, lista_bases_geral).encode('utf-8'))
                    mapas_gerados.append(f"Rota_{nome_seguro}_{data_atual}.kml")
        zip_kml_bytes = buf_zip_kml.getvalue()

        with st.expander("📄 Ver lista de arquivos gerados (Conteúdo dos ZIPs)"):
            st.markdown("**Planilhas Excel:** " + ", ".join(planilhas_geradas))
            st.markdown("**Mapas KML:** " + ", ".join(mapas_gerados))

        col_b1, col_b2, col_b3 = st.columns([1, 1, 1])
        col_b1.download_button("🌐 1. Planilhas Roteirizadas (ZIP)", data=zip_xl_bytes, file_name=f"Dados_Estruturados_Roteiro_{data_atual}.zip", mime="application/zip", use_container_width=True)
        col_b2.download_button("🗺️ 2. Baixar Mapas (KML ZIP)", data=zip_kml_bytes, file_name=f"Mapas_KML_{data_atual}.zip", mime="application/zip", use_container_width=True)
        if col_b3.button("🧹 Zerar Roteirizador", type="primary", use_container_width=True):
            limpar_roteirizador()
            
        return 

    # -------------------------------------------------------------
    # 2. MÁQUINA DE ESTADOS - ROTEAMENTO CONTÍNUO E BATCH
    # -------------------------------------------------------------
    if status_exec in ["RUNNING", "PAUSED"]:
        st.markdown("## 🚀 Execução do Motor de Roteirização")
        st.markdown("O sistema está conectando via satélite para agrupar e traçar os percursos.")
        
        c1, c2 = st.columns(2)
        if status_exec == "RUNNING":
            if c1.button("⏸️ Pausar Roteirização", use_container_width=True):
                st.session_state.vrp_status = "PAUSED"
                st.rerun()
        else:
            st.warning("⚠️ **Processo Pausado.** Clique em Retomar quando a internet estiver estável.")
            if c1.button("▶️ Retomar", use_container_width=True, type="primary"):
                st.session_state.vrp_state['last_time'] = time.time()
                st.session_state.vrp_status = "RUNNING"
                st.rerun()
                
        if c2.button("⏹️ Parar (Descartar)", use_container_width=True):
            limpar_roteirizador()
            
        state = st.session_state.vrp_state
        cfg = state['config']
        total = state['total_obras']
        progresso = min(state['obras_processadas'] / total, 1.0) if total > 0 else 1.0
        
        st.progress(progresso)
        
        if state['obras_processadas'] > 0:
            avg = state['tempo_processamento'] / state['obras_processadas']
            restantes = total - state['obras_processadas']
            est_rem = avg * restantes
            m, s = divmod(int(est_rem), 60)
            h, m = divmod(m, 60)
            time_str = f"{h:02d}h {m:02d}m {s:02d}s" if h > 0 else f"{m:02d}m {s:02d}s"
            
            with timer_placeholder.container():
                st.markdown("### ⏱️ Tempo Restante")
                if st.session_state.vrp_status == "PAUSED":
                    st.warning(f"⏸️ **Pausado**\n\nFaltavam {time_str}")
                else:
                    html_timer = f"""
                    <style>
                    @keyframes flip-glass {{
                        0% {{ transform: rotate(0deg); }}
                        40% {{ transform: rotate(180deg); }}
                        50% {{ transform: rotate(180deg); }}
                        90% {{ transform: rotate(360deg); }}
                        100% {{ transform: rotate(360deg); }}
                    }}
                    .hourglass-anim {{
                        display: inline-block;
                        animation: flip-glass 2.5s ease-in-out infinite;
                    }}
                    .timer-alert {{
                        padding: 0.75rem 1rem;
                        border-radius: 0.5rem;
                        background-color: rgba(46, 123, 50, 0.15);
                        color: #176B2C;
                        border: 1px solid rgba(46, 123, 50, 0.3);
                        display: flex;
                        align-items: center;
                        font-family: sans-serif;
                    }}
                    @media (prefers-color-scheme: dark) {{
                        .timer-alert {{
                            background-color: rgba(60, 179, 113, 0.15);
                            color: #66bb6a;
                            border: 1px solid rgba(60, 179, 113, 0.3);
                        }}
                    }}
                    </style>
                    <div class="timer-alert">
                        <span class="hourglass-anim" style="font-size:1.5rem; margin-right:12px;">⏳</span> 
                        <strong style="font-size:1.2rem; letter-spacing: 0.5px;">{time_str}</strong>
                    </div>
                    """
                    st.markdown(html_timer, unsafe_allow_html=True)
        else:
            with timer_placeholder.container():
                st.markdown("### ⏱️ Tempo Restante")
                st.markdown("""
                <style>
                @keyframes pulse-text {
                    0% { opacity: 0.4; }
                    50% { opacity: 1; }
                    100% { opacity: 0.4; }
                }
                .calculating-alert {
                    padding: 0.75rem 1rem;
                    border-radius: 0.5rem;
                    background-color: rgba(26, 79, 124, 0.15);
                    color: #1A4F7C;
                    border: 1px solid rgba(26, 79, 124, 0.3);
                    font-weight: 600;
                    display: flex;
                    align-items: center;
                }
                @media (prefers-color-scheme: dark) {
                    .calculating-alert { color: #64B5F6; }
                }
                .spin-icon {
                    display: inline-block;
                    animation: pulse-text 1.5s infinite;
                }
                </style>
                <div class="calculating-alert">
                    <span class="spin-icon" style="font-size:1.2rem; margin-right:10px;">🔄</span> 
                    <span style="animation: pulse-text 1.5s infinite;">Calculando estimativa...</span>
                </div>
                """, unsafe_allow_html=True)
        
        if st.session_state.vrp_status == "RUNNING":
            agora = time.time()
            state['tempo_processamento'] += (agora - state['last_time'])
            state['last_time'] = agora
            
            status_text = st.empty()
            df_todas_bases_ativas = pd.DataFrame(st.session_state.bases_records)
            
            if state['fase'] == 'INIT':
                if state['b_idx'] >= len(state['b_names']):
                    state['fase'] = 'DONE'
                else:
                    b_name = state['b_names'][state['b_idx']]
                    base_ref = df_todas_bases_ativas[df_todas_bases_ativas['LEVANTADOR'] == b_name].iloc[0]
                    if pd.isna(base_ref.get('LATITUDE')):
                        state['b_idx'] += 1
                    else:
                        state['base_lat'] = float(base_ref['LATITUDE'])
                        state['base_lon'] = float(base_ref['LONGITUDE'])
                        state['start_lat'] = state['base_lat']
                        state['start_lon'] = state['base_lon']
                        state['periodo_atual'] = 1
                        state['ordem_absoluta'] = 1
                        state['fase'] = 'BUILD_DAY'
                st.rerun()
                
            elif state['fase'] == 'BUILD_DAY':
                b_name = state['b_names'][state['b_idx']]
                status_text.info(f"🧠 Organizando {cfg['tipo_periodo']} {state['periodo_atual']} para **{b_name}**...")
                
                unvisited = state['unvisited']
                unv_b = unvisited[unvisited['BASE_ATRIBUIDA'] == b_name]
                
                if unv_b.empty:
                    state['fase'] = 'END_TEAM'
                    st.rerun()
                    
                if state['periodo_atual'] > cfg['limite_periodos']:
                    state['obras_sobra_total'] += len(unv_b)
                    state['obras_processadas'] += len(unv_b)
                    state['unvisited'] = unvisited[unvisited['BASE_ATRIBUIDA'] != b_name]
                    state['fase'] = 'END_TEAM'
                    st.rerun()
                
                dia_obras_prio = []
                dia_obras_norm = []
                tempo_dia = 0.0
                qtd_dia = 0
                km_dia = 0.0
                curr_lat, curr_lon = state['base_lat'], state['base_lon']
                unv_b_temp = unv_b.copy()
                
                while not unv_b_temp.empty:
                    unvisited_prio = unv_b_temp[unv_b_temp['PRIORIDADE'] == 'Sim']
                    if not unvisited_prio.empty:
                        dists = haversine_vectorized(curr_lat, curr_lon, unvisited_prio['LATITUDE'].values, unvisited_prio['LONGITUDE'].values)
                        nearest_idx = unvisited_prio.index[dists.argmin()]
                        is_prio = True
                    else:
                        dists = haversine_vectorized(curr_lat, curr_lon, unv_b_temp['LATITUDE'].values, unv_b_temp['LONGITUDE'].values)
                        nearest_idx = unv_b_temp.index[dists.argmin()]
                        is_prio = False
                        
                    nearest_row = unv_b_temp.loc[nearest_idx]
                    dist_km = round(dists.min(), 2)
                    is_rural = False
                    if 'LOCALIDADE' in nearest_row and str(nearest_row['LOCALIDADE']).upper() == 'RURAL': is_rural = True
                    if 'TIPO NOTA' in nearest_row and str(nearest_row['TIPO NOTA']).upper() == 'UNR': is_rural = True
                    
                    tempo_viagem_h = (dist_km / cfg['velocidade_media_kmh']) * (1.6 if is_rural else 1.0)
                    tempo_necessario = tempo_viagem_h + cfg['tempo_medio_obra']
                    dist_retorno_est = haversine_vectorized(nearest_row['LATITUDE'], nearest_row['LONGITUDE'], state['base_lat'], state['base_lon'])
                    
                    if cfg['modo_limite'] == "Quantidade Fixa de Obras" and qtd_dia >= cfg['obras_por_periodo']: break
                    if cfg['modo_limite'] != "Quantidade Fixa de Obras" and tempo_dia + tempo_necessario > cfg['horas_por_dia'] and qtd_dia > 0: break
                    if (km_dia + dist_km + dist_retorno_est) > cfg['limite_km_diario'] and qtd_dia > 0: break
                        
                    if is_prio: dia_obras_prio.append(nearest_row.to_dict())
                    else: dia_obras_norm.append(nearest_row.to_dict())
                    
                    curr_lat, curr_lon = nearest_row['LATITUDE'], nearest_row['LONGITUDE']
                    unv_b_temp = unv_b_temp.drop(nearest_idx)
                    tempo_dia += tempo_necessario
                    km_dia += dist_km
                    qtd_dia += 1
                
                if len(dia_obras_prio) == 0 and len(dia_obras_norm) == 0:
                    state['fase'] = 'END_TEAM'
                    st.rerun()
                    
                last_prio_lat, last_prio_lon = state['base_lat'], state['base_lon']
                if len(dia_obras_prio) > 0:
                    last_prio_lat, last_prio_lon = dia_obras_prio[-1]['LATITUDE'], dia_obras_prio[-1]['LONGITUDE']
                    
                dia_obras_norm = otimizar_rota_tsp_2opt(dia_obras_norm, last_prio_lat, last_prio_lon)
                state['dia_final'] = dia_obras_prio + dia_obras_norm
                
                state['unvisited'] = unvisited.drop(unv_b.index.difference(unv_b_temp.index))
                state['start_lat'] = state['base_lat']
                state['start_lon'] = state['base_lon']
                state['tempo_acumulado_rota'] = 0.0
                state['almoco_inserido'] = False
                state['fase'] = 'PROCESS_DAY'
                st.rerun()

            elif state['fase'] == 'PROCESS_DAY':
                b_name = state['b_names'][state['b_idx']]
                lote_tamanho = 5 
                obras_processadas_agora = 0

                while len(state['dia_final']) > 0 and obras_processadas_agora < lote_tamanho:
                    obra = state['dia_final'].pop(0) 
                    
                    if cfg['modo_limite'] != "Quantidade Fixa de Obras" and state['tempo_acumulado_rota'] >= 4.0 and not state['almoco_inserido']:
                        state['routed_data'].append({
                            'PROTOCOLO': 'PAUSA_ALMOCO', 'NOME DO SOLICITANTE': '🍔 HORÁRIO DE ALMOÇO (1h)',
                            'LATITUDE': state['start_lat'], 'LONGITUDE': state['start_lon'], 'BASE_ATRIBUIDA': b_name, 'ORDEM': state['ordem_absoluta'],
                            'SEMANA': state['periodo_atual'] if cfg['tipo_periodo'] == "Semana" else 1, 'DIA': state['periodo_atual'] if cfg['tipo_periodo'] == "Dia" else 1,
                            'PERIODO': state['periodo_atual'], 'DISTANCIA_PONTO_ANTERIOR_KM': 0.0, 'TEMPO_VIAGEM_MINUTOS': 60.0,
                            'ROTA_GEOMETRIA': [[state['start_lon'], state['start_lat']], [state['start_lon'], state['start_lat']]], 'PRIORIDADE': 'Não'
                        })
                        state['almoco_inserido'] = True
                        state['ordem_absoluta'] += 1
                        state['tempo_acumulado_rota'] += 1.0
                        
                    status_text.warning(f"🗺️ Mapeando lote de rotas para **{b_name}** | Progresso atual: {state['ordem_absoluta']}")
                    
                    rota_geom, dur_sec = obter_rota_ruas(state['start_lat'], state['start_lon'], obra['LATITUDE'], obra['LONGITUDE'], cfg['velocidade_media_kmh'])
                    
                    is_rur = False
                    if 'LOCALIDADE' in obra and str(obra['LOCALIDADE']).upper() == 'RURAL': is_rur = True
                    if 'TIPO NOTA' in obra and str(obra['TIPO NOTA']).upper() == 'UNR': is_rur = True
                    if is_rur: dur_sec *= 1.6
                    
                    obra['ORDEM'] = state['ordem_absoluta']
                    obra['SEMANA'] = state['periodo_atual'] if cfg['tipo_periodo'] == "Semana" else 1
                    obra['DIA'] = state['periodo_atual'] if cfg['tipo_periodo'] == "Dia" else 1
                    obra['PERIODO'] = state['periodo_atual']
                    obra['DISTANCIA_PONTO_ANTERIOR_KM'] = round(haversine_vectorized(state['start_lat'], state['start_lon'], obra['LATITUDE'], obra['LONGITUDE']), 2)
                    obra['TEMPO_VIAGEM_MINUTOS'] = round(dur_sec / 60.0, 1)
                    obra['ROTA_GEOMETRIA'] = rota_geom
                    
                    state['routed_data'].append(obra)
                    state['start_lat'] = obra['LATITUDE']
                    state['start_lon'] = obra['LONGITUDE']
                    state['ordem_absoluta'] += 1
                    state['tempo_acumulado_rota'] += (dur_sec / 3600.0) + cfg['tempo_medio_obra']
                    state['obras_processadas'] += 1
                    
                    obras_processadas_agora += 1
                    time.sleep(0.02)
                
                if len(state['dia_final']) == 0:
                    state['fase'] = 'END_DAY'
                    
                st.rerun()

            elif state['fase'] == 'END_DAY':
                b_name = state['b_names'][state['b_idx']]
                status_text.success(f"🏠 Fechando {cfg['tipo_periodo']} {state['periodo_atual']} de **{b_name}**, traçando retorno...")
                
                rota_retorno, dur_ret_seg = obter_rota_ruas(state['start_lat'], state['start_lon'], state['base_lat'], state['base_lon'], cfg['velocidade_media_kmh'])
                dist_retorno = haversine_vectorized(state['start_lat'], state['start_lon'], state['base_lat'], state['base_lon'])
                
                state['routed_data'].append({
                    'PROTOCOLO': 'RETORNO_BASE', 'NOME DO SOLICITANTE': 'BASE_RETORNO', 'LATITUDE': state['base_lat'], 'LONGITUDE': state['base_lon'],
                    'BASE_ATRIBUIDA': b_name, 'ORDEM': state['ordem_absoluta'], 'SEMANA': state['periodo_atual'] if cfg['tipo_periodo'] == "Semana" else 1,
                    'DIA': state['periodo_atual'] if cfg['tipo_periodo'] == "Dia" else 1, 'PERIODO': state['periodo_atual'],
                    'DISTANCIA_PONTO_ANTERIOR_KM': round(dist_retorno, 2), 'TEMPO_VIAGEM_MINUTOS': round(dur_ret_seg / 60.0, 1),
                    'ROTA_GEOMETRIA': rota_retorno, 'PRIORIDADE': 'Não'
                })
                
                state['periodo_atual'] += 1
                state['ordem_absoluta'] = 1
                state['fase'] = 'BUILD_DAY'
                st.rerun()

            elif state['fase'] == 'END_TEAM':
                state['b_idx'] += 1
                state['fase'] = 'INIT'
                st.rerun()
                
            elif state['fase'] == 'DONE':
                status_text.success("✅ Roteirização finalizada! Preparando arquivos finais...")
                
                if state['obras_sobra_total'] > 0:
                    st.warning(f"⏳ {state['obras_sobra_total']} obras ficaram de fora do roteiro porque a carga horária/limite estourou.")
                    time.sleep(2) 
                    
                df_final_route = pd.DataFrame(state['routed_data'])
                if not df_final_route.empty:
                    df_final_route['DISTANCIA_PROXIMO_PONTO_KM'] = df_final_route.groupby(['BASE_ATRIBUIDA', 'PERIODO'])['DISTANCIA_PONTO_ANTERIOR_KM'].shift(-1).fillna(0.0)
                    
                st.session_state.df_routed = df_final_route
                st.session_state.roteamento_concluido = True
                st.session_state.vrp_status = "IDLE"
                st.rerun()
                
        return 

    # -------------------------------------------------------------
    # 3. TELA DE CONFIGURAÇÃO INICIAL (Uploads e Ajustes)
    # -------------------------------------------------------------
    col_up_1, col_up_2 = st.columns(2)

    with col_up_1:
        st.markdown("### 👥 1. Levantadores Principais")
        df_bases = pd.DataFrame()

        # =============================================================
        # DELIMITAÇÃO VISUAL POR CONTAINERS NATIVOS (ST.CONTAINER)
        # =============================================================
        with st.container(border=True):
            base_file = st.file_uploader("Suba a planilha Levantadores_MA", type=["xlsx", "xls"])
        
        if base_file:
            try:
                df_bases_temp_ui = pd.read_excel(base_file)
                df_bases_temp_ui.columns = normalize_cols(df_bases_temp_ui.columns)
                if 'LEVANTADOR' not in df_bases_temp_ui.columns:
                    for p_nome in ['NOME', 'TECNICO', 'EQUIPE', 'COLABORADOR']:
                        if p_nome in df_bases_temp_ui.columns:
                            df_bases_temp_ui = df_bases_temp_ui.rename(columns={p_nome: 'LEVANTADOR'})
                            break
                if 'LEVANTADOR' in df_bases_temp_ui.columns:
                    opcoes_levs = sorted([str(x) for x in df_bases_temp_ui['LEVANTADOR'].dropna().unique().tolist() if str(x).upper().strip() != 'SEM LEVANTADOR'])
                    levs_selecionados = st.multiselect("Selecione as Equipes Principais:", opcoes_levs)
                    if levs_selecionados:
                        df_bases = df_bases_temp_ui[df_bases_temp_ui['LEVANTADOR'].isin(levs_selecionados)].copy()
                        if 'RESIDENCIA' in df_bases.columns:
                            muns_unicos = df_bases['RESIDENCIA'].dropna().unique()
                            mapa_coords = {}
                            with st.spinner("🌍 Mapeando coordenadas dos municípios-base (Satélite)..."):
                                for mun in muns_unicos:
                                    lat, lon = obter_coordenadas_municipio_cached(mun)
                                    mapa_coords[mun] = (lat, lon)
                            df_bases['LATITUDE'] = df_bases['RESIDENCIA'].map(lambda x: mapa_coords.get(x, (np.nan, np.nan))[0])
                            df_bases['LONGITUDE'] = df_bases['RESIDENCIA'].map(lambda x: mapa_coords.get(x, (np.nan, np.nan))[1])
                        else:
                            df_bases['LATITUDE'] = pd.to_numeric(df_bases.get('LATITUDE', pd.Series()).astype(str).str.replace(',', '.'), errors='coerce')
                            df_bases['LONGITUDE'] = pd.to_numeric(df_bases.get('LONGITUDE', pd.Series()).astype(str).str.replace(',', '.'), errors='coerce')
                            
                        df_bases = df_bases.dropna(subset=['LATITUDE', 'LONGITUDE'])
                        df_bases['TIPO_EQUIPE'] = 'PRINCIPAL'
            except Exception as e:
                st.error(f"Erro ao ler a planilha: {e}")

        st.markdown("##### Regra de Atribuição Territorial")
        tipo_atribuicao = st.radio("Regra", ["Clusterização Inteligente por IA (K-Means VRP)", "Por Proximidade Geográfica das Coordenadas (Ignora texto)", "Por Municípios Atendidos (Lê texto da planilha)"], index=2, label_visibility="collapsed")

    with col_up_2:
        st.markdown("### 📁 2. Upload de Demandas (Obras)")
        
        with st.container(border=True):
            task_files = st.file_uploader("1️⃣ Base Principal (Planilha de Obras Antiga/Original)", type=["xlsx", "xls"], accept_multiple_files=True)
        
        st.markdown("##### 🔄 Atualização Rápida de Status (Opcional)")
        
        with st.container(border=True):
            status_file = st.file_uploader("2️⃣ Planilha Atualizada do SharePoint", type=["xlsx", "xls"])
        
        df_status_upload = pd.DataFrame()
        coluna_status_selecionada = None
        
        if status_file:
            try:
                df_status_upload = pd.read_excel(status_file)
                cols_status = df_status_upload.columns.tolist()
                def_idx = 4 if len(cols_status) >= 5 else 0
                coluna_status_selecionada = st.selectbox("📌 Qual coluna contém o Status Atualizado?", cols_status, index=def_idx)
            except Exception as e:
                st.error(f"Erro ao ler planilha de status: {e}")
        
        st.markdown("##### 🧑‍🤝‍🧑 3. Equipes de Apoio (Temporários - Opcional)")
        st.caption("Recebem APENAS obras comuns. O volume de trabalho é dividido nas mesmas regiões das equipes principais.")
        
        with st.container(border=True):
            temp_bases_files = st.file_uploader("Suba a(s) planilha(s) de Levantadores Temporários", type=["xlsx", "xls"], accept_multiple_files=True)
        
        df_bases_temp = pd.DataFrame()
        if temp_bases_files:
            try:
                dfs_temp = []
                for f in temp_bases_files:
                    df_t = pd.read_excel(f)
                    df_t.columns = normalize_cols(df_t.columns)
                    if 'LEVANTADOR' not in df_t.columns:
                        for p_nome in ['NOME', 'TECNICO', 'EQUIPE', 'COLABORADOR']:
                            if p_nome in df_t.columns:
                                df_t = df_t.rename(columns={p_nome: 'LEVANTADOR'})
                                break
                    dfs_temp.append(df_t)
                df_bases_temp_full = pd.concat(dfs_temp, ignore_index=True)
                
                if 'LEVANTADOR' in df_bases_temp_full.columns:
                    opcoes_levs_temp = sorted([str(x) for x in df_bases_temp_full['LEVANTADOR'].dropna().unique().tolist() if str(x).upper().strip() != 'SEM LEVANTADOR'])
                    levs_temp_selecionados = st.multiselect("Selecione as Equipes Temporárias:", opcoes_levs_temp, key="ms_temp")
                    
                    if levs_temp_selecionados:
                        df_bases_temp = df_bases_temp_full[df_bases_temp_full['LEVANTADOR'].isin(levs_temp_selecionados)].copy()
                        if 'RESIDENCIA' in df_bases_temp.columns:
                            muns_unicos_temp = df_bases_temp['RESIDENCIA'].dropna().unique()
                            mapa_coords_temp = {}
                            with st.spinner("🌍 Mapeando bases dos temporários..."):
                                for mun in muns_unicos_temp:
                                    lat, lon = obter_coordenadas_municipio_cached(mun)
                                    mapa_coords_temp[mun] = (lat, lon)
                            df_bases_temp['LATITUDE'] = df_bases_temp['RESIDENCIA'].map(lambda x: mapa_coords_temp.get(x, (np.nan, np.nan))[0])
                            df_bases_temp['LONGITUDE'] = df_bases_temp['RESIDENCIA'].map(lambda x: mapa_coords_temp.get(x, (np.nan, np.nan))[1])
                        else:
                            df_bases_temp['LATITUDE'] = pd.to_numeric(df_bases_temp.get('LATITUDE', pd.Series()).astype(str).str.replace(',', '.'), errors='coerce')
                            df_bases_temp['LONGITUDE'] = pd.to_numeric(df_bases_temp.get('LONGITUDE', pd.Series()).astype(str).str.replace(',', '.'), errors='coerce')
                        
                        df_bases_temp = df_bases_temp.dropna(subset=['LATITUDE', 'LONGITUDE'])
                        df_bases_temp['TIPO_EQUIPE'] = 'TEMPORARIA'
            except Exception as e:
                st.error(f"Erro ao ler temporários: {e}")

        if not task_files: st.info("Aguardando upload para habilitar a configuração."); return

        try:
            dfs = []
            for f in task_files:
                df_temp = pd.read_excel(f)
                if len(dfs) == 0: st.session_state.colunas_originais = df_temp.columns.tolist()
                df_temp.columns = normalize_cols(df_temp.columns)
                dfs.append(df_temp)
            df_tasks = pd.concat(dfs, ignore_index=True)
        except Exception as e:
            st.error(f"Erro ao unificar as planilhas: {e}"); return

        if not df_status_upload.empty and coluna_status_selecionada:
            df_tasks = atualizar_status_via_df(df_tasks, df_status_upload, coluna_status_selecionada)

    if 'LATITUDE' not in df_tasks.columns or 'LONGITUDE' not in df_tasks.columns:
        st.error("❌ A planilha de Obras precisa ter LATITUDE e LONGITUDE."); return

    st.markdown("---")
    
    # === SELEÇÃO INTERATIVA DE STATUS ===
    if 'STATUS LIST' in df_tasks.columns:
        df_tasks['STATUS_LIMPO'] = df_tasks['STATUS LIST'].astype(str).str.strip().str.upper()
        status_unicos = sorted(df_tasks['STATUS_LIMPO'].unique().tolist())
        padroes_ativos = [s for s in status_unicos if s in STATUS_PADRAO]
        
        status_selecionados = st.multiselect(
            "📌 Selecione os Status que devem ser incluídos na Roteirização:",
            options=status_unicos,
            default=padroes_ativos
        )
        
        if not status_selecionados:
            st.warning("⚠️ Você precisa selecionar pelo menos um status para prosseguir.")
            return
            
        df_tasks = df_tasks[df_tasks['STATUS_LIMPO'].isin(status_selecionados)]
        df_tasks = df_tasks.drop(columns=['STATUS_LIMPO'])

    # === LIMPEZA DE BASE ===
    total_orig = len(df_tasks)
    df_tasks['LATITUDE'] = pd.to_numeric(df_tasks['LATITUDE'].astype(str).str.replace(',', '.'), errors='coerce')
    df_tasks['LONGITUDE'] = pd.to_numeric(df_tasks['LONGITUDE'].astype(str).str.replace(',', '.'), errors='coerce')
    df_tasks = df_tasks.dropna(subset=['LATITUDE', 'LONGITUDE'])
    df_tasks = df_tasks[(df_tasks['LATITUDE'] != 0.0) & (df_tasks['LONGITUDE'] != 0.0)]
    
    for col_nome in ['NOME', 'NOME DO SOLICITANTE', 'CLIENTE']:
        if col_nome in df_tasks.columns:
            df_tasks = df_tasks.dropna(subset=[col_nome])
            df_tasks = df_tasks[df_tasks[col_nome].astype(str).str.strip() != '']
            
    if 'STATUS SAP' in df_tasks.columns:
        df_tasks = df_tasks[~df_tasks['STATUS SAP'].astype(str).str.strip().str.upper().isin(['CANC', 'FINL'])]

    if 'TIPO NOTA' in df_tasks.columns:
        df_tasks['PRIORIDADE'] = df_tasks['TIPO NOTA'].apply(lambda x: 'Sim' if str(x).strip().upper() in TIPOS_PRIORITARIOS else 'Não')
    else:
        df_tasks['PRIORIDADE'] = 'Não'

    if total_orig - len(df_tasks) > 0:
        st.warning(f"⚠️ {total_orig - len(df_tasks)} obras com erros sistêmicos (sem coordenadas ou campos de Nome vazios) foram ignoradas. Restam **{len(df_tasks)} válidas.**")

    if df_tasks.empty: return

    # === PRÉ-ALOCAÇÃO TERRITORIAL EXCLUSIVA ===
    df_tasks_alocadas = pd.DataFrame()
    bases_principais_records = df_bases.to_dict('records') if not df_bases.empty else []
    bases_temporarias_records = df_bases_temp.to_dict('records') if not df_bases_temp.empty else []
    todas_bases_records = bases_principais_records + bases_temporarias_records
    
    if len(todas_bases_records) > 0:
        df_tasks['BASE_ATRIBUIDA'] = "NÃO ALOCADO"
        
        df_prio = df_tasks[df_tasks['PRIORIDADE'] == 'Sim'].copy()
        df_comum = df_tasks[df_tasks['PRIORIDADE'] == 'Não'].copy()
        
        if tipo_atribuicao == "Clusterização Inteligente por IA (K-Means VRP)":
            def allocate_kmeans(df_subset, base_list):
                if df_subset.empty or not base_list: return df_subset
                k = len(base_list)
                if k > 0 and len(df_subset) >= k:
                    coords = df_subset[['LATITUDE', 'LONGITUDE']].values
                    labels, centroids = kmeans_clustering(coords, k)
                    base_coords = {b['LEVANTADOR']: (float(b['LATITUDE']), float(b['LONGITUDE'])) for b in base_list if pd.notna(b.get('LATITUDE'))}
                    used_bases = set()
                    
                    for i, centroid in enumerate(centroids):
                        best_base = None
                        min_dist = float('inf')
                        for b_name, (b_lat, b_lon) in base_coords.items():
                            if b_name in used_bases: continue
                            dist = haversine_vectorized(centroid[0], centroid[1], b_lat, b_lon)
                            if dist < min_dist: min_dist, best_base = dist, b_name
                        if best_base:
                            used_bases.add(best_base)
                            df_subset.loc[df_subset.index[labels == i], 'BASE_ATRIBUIDA'] = best_base
                else:
                    for idx, row in df_subset.iterrows():
                        best_dist, best_b = float('inf'), "NÃO ALOCADO"
                        for b in base_list:
                            d = haversine_vectorized(row['LATITUDE'], row['LONGITUDE'], float(b['LATITUDE']), float(b['LONGITUDE']))
                            if d < best_dist: best_dist, best_b = d, b['LEVANTADOR']
                        df_subset.loc[idx, 'BASE_ATRIBUIDA'] = best_b
                return df_subset

            df_prio = allocate_kmeans(df_prio, bases_principais_records)
            df_comum = allocate_kmeans(df_comum, todas_bases_records)
            df_tasks = pd.concat([df_prio, df_comum])

        elif tipo_atribuicao == "Por Proximidade Geográfica das Coordenadas (Ignora texto)":
            def get_nearest_base(lat, lon, base_list):
                if not base_list: return "NÃO ALOCADO"
                min_dist, best_base = float('inf'), None
                for b in base_list:
                    if pd.notna(b.get('LATITUDE')):
                        d = haversine_vectorized(lat, lon, float(b['LATITUDE']), float(b['LONGITUDE']))
                        if d < min_dist: min_dist, best_base = d, b['LEVANTADOR']
                return best_base if best_base else "NÃO ALOCADO"
                
            df_prio['BASE_ATRIBUIDA'] = df_prio.apply(lambda r: get_nearest_base(r['LATITUDE'], r['LONGITUDE'], bases_principais_records), axis=1)
            df_comum['BASE_ATRIBUIDA'] = df_comum.apply(lambda r: get_nearest_base(r['LATITUDE'], r['LONGITUDE'], todas_bases_records), axis=1)
            df_tasks = pd.concat([df_prio, df_comum])

        elif tipo_atribuicao == "Por Municípios Atendidos (Lê texto da planilha)":
            mun_to_main = {}
            mun_to_all = {}
            
            for b in todas_bases_records:
                for m in str(b.get('MUNICIPIO', '')).split(','):
                    m_limpo = normalizar_municipios(pd.Series([m])).iloc[0]
                    if m_limpo:
                        if m_limpo not in mun_to_all: mun_to_all[m_limpo] = []
                        mun_to_all[m_limpo].append(b['LEVANTADOR'])
                        if b.get('TIPO_EQUIPE') == 'PRINCIPAL':
                            if m_limpo not in mun_to_main: mun_to_main[m_limpo] = []
                            mun_to_main[m_limpo].append(b['LEVANTADOR'])
            
            def allocate_by_mun_divided(df_sub, map_dict):
                if df_sub.empty: return df_sub
                df_sub = df_sub.copy()
                df_sub['MUN_LIMPO'] = normalizar_municipios(df_sub['MUNICIPIO'])
                df_sub['BASE_ATRIBUIDA'] = "NÃO ALOCADO"
                for mun, group in df_sub.groupby('MUN_LIMPO'):
                    bases_disp = map_dict.get(mun, [])
                    if bases_disp:
                        n_bases = len(bases_disp)
                        assigned = [bases_disp[i % n_bases] for i in range(len(group))]
                        df_sub.loc[group.index, 'BASE_ATRIBUIDA'] = assigned
                return df_sub.drop(columns=['MUN_LIMPO'])
                
            df_prio = allocate_by_mun_divided(df_prio, mun_to_main)
            df_comum = allocate_by_mun_divided(df_comum, mun_to_all)
            
            df_tasks = pd.concat([df_prio, df_comum])

        df_unallocated = df_tasks[df_tasks['BASE_ATRIBUIDA'] == "NÃO ALOCADO"]
        df_tasks_alocadas = df_tasks[df_tasks['BASE_ATRIBUIDA'] != "NÃO ALOCADO"].copy()

        if df_tasks_alocadas.empty:
            st.error("Falha: Nenhuma obra encontrada no território das equipes selecionadas. Troque a regra ou o Levantador.")
            return

        if not df_unallocated.empty:
            st.warning(f"⚠️ {len(df_unallocated)} obras carregadas ficaram sem Levantador. Motivos possíveis: Não pertencem à região das equipes ou são prioritárias e não havia equipe Principal alocada.")
            
        bases_records = todas_bases_records 

    # === CONFIGURAÇÃO DE EXIBIÇÃO ===
    if not df_tasks_alocadas.empty:
        with st.expander("🛠️ 4. Configuração de Roteirização (Filtros)", expanded=True):
            c_ex1, c_ex2 = st.columns(2)
            
            if 'TIPO NOTA' in df_tasks_alocadas.columns:
                tipos_nota_unicos = sorted(df_tasks_alocadas['TIPO NOTA'].astype(str).dropna().unique().tolist())
                tipos_selecionados = c_ex1.multiselect("🏷️ Filtrar TIPO DE NOTA (Opcional):", tipos_nota_unicos, default=tipos_nota_unicos)
                if not tipos_selecionados:
                    st.warning("Selecione pelo menos um Tipo de Nota para prosseguir."); return
                df_tasks_alocadas = df_tasks_alocadas[df_tasks_alocadas['TIPO NOTA'].astype(str).isin(tipos_selecionados)]

            todas_cols = df_tasks_alocadas.columns.tolist()
            cols_desejadas = ['PROTOCOLO', 'NOME', 'ENDEREÇO', 'MUNICIPIO', 'INFORMAÇÕES EXTRAS', 'LATITUDE', 'LONGITUDE', 'TIPO NOTA']
            cols_desejadas_norm = normalize_cols(cols_desejadas)
            cols_padrao = [c for c in cols_desejadas_norm if c in todas_cols]
            
            colunas_exibir = c_ex1.multiselect("Colunas para aparecer no Balão do KML", todas_cols, default=cols_padrao)
            c_ex2.info(f"⚡ **Prioridade Automática Ativada:** Obras com TIPO NOTA igual a {', '.join(TIPOS_PRIORITARIOS)} recebem pino vermelho e são roteirizadas apenas para Equipes Principais.")
            col_prioridade = "TIPO NOTA"

    # =============================================================
    # BOTÃO PARA ATIVAR A MÁQUINA DE ESTADOS
    # =============================================================
    if st.button("🚀 Iniciar Motor de Roteirização", type="primary", use_container_width=True):
        if df_tasks_alocadas.empty:
            st.error("Selecione equipes e regras compatíveis com a planilha primeiro.")
            return

        if 'TIPO NOTA' in df_tasks_alocadas.columns and 'TIPO NOTA' not in colunas_exibir:
            colunas_exibir.append('TIPO NOTA')

        st.session_state.bases_records = bases_records
        st.session_state.tipo_periodo = tipo_periodo
        st.session_state.colunas_exibir = colunas_exibir
        st.session_state.col_prioridade = col_prioridade
        
        st.session_state.vrp_state = {
            'fase': 'INIT',
            'config': {
                'limite_periodos': limite_periodos,
                'modo_limite': modo_limite,
                'velocidade_media_kmh': velocidade_media_kmh,
                'tempo_medio_obra': tempo_medio_obra,
                'horas_por_dia': horas_por_dia,
                'limite_km_diario': limite_km_diario,
                'obras_por_periodo': obras_por_periodo,
                'tipo_periodo': tipo_periodo
            },
            'b_names': list(set([b['LEVANTADOR'] for b in bases_records])),
            'b_idx': 0,
            'unvisited': df_tasks_alocadas.copy(),
            'routed_data': [],
            'dia_final': [],
            'periodo_atual': 1,
            'ordem_absoluta': 1,
            'base_lat': None, 'base_lon': None,
            'start_lat': None, 'start_lon': None,
            'tempo_acumulado_rota': 0.0,
            'almoco_inserido': False,
            'obras_processadas': 0,
            'obras_sobra_total': 0,
            'total_obras': len(df_tasks_alocadas),
            'tempo_processamento': 0.0,
            'last_time': time.time()
        }
        
        st.session_state.vrp_status = "RUNNING"
        st.rerun()

if __name__ == "__main__":
    view_roteirizador()
