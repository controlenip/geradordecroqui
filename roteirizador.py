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

# === IMPORTAÇÃO DO MOTOR VRP DO GOOGLE ===
try:
    from ortools.constraint_solver import routing_enums_pb2
    from ortools.constraint_solver import pywrapcp
except ImportError:
    st.error("🚨 Biblioteca 'ortools' não encontrada. Instale executando: pip install ortools")
    st.stop()

# ==========================================
# CONSTANTES DE NEGÓCIO
# ==========================================
STATUS_PADRAO = ['EM LEVANTAMENTO', '0', 'SEM INFORMAÇÕES', 'SEM INFORMACOES', 'CORREÇÃO DE LEVANTAMENTO', 'CORRECAO DE LEVANTAMENTO', 'PRÉ ANÁLISE', 'PRE ANALISE']
TIPOS_PRIORITARIOS = ["CCF", "DIF", "MGD", "MTP", "ASC", "SID"]

# ==========================================
# CONFIGURAÇÃO DE SESSÃO WEB E RETENTATIVAS
# ==========================================
def get_retry_session(retries=3, backoff_factor=0.5):
    session = requests.Session()
    retry = Retry(total=retries, read=retries, connect=retries, backoff_factor=backoff_factor, status_forcelist=(429, 500, 502, 503, 504))
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

http_session = get_retry_session()

st.set_page_config(page_title="Roteirizador Operacional Pro", page_icon="🚙", layout="wide")

# ==========================================
# INJEÇÃO DE CSS GLOBAL & COMPONENTES PREMIUM
# ==========================================
st.markdown("""
<style>
    .block-container { padding-top: 4rem !important; padding-bottom: 2rem !important; }
    .stSelectbox label, .stFileUploader label, .stRadio label, .stNumberInput label, .stMultiSelect label { font-size: 14px !important; font-weight: 600 !important; color: #1A4F7C !important; }
    .stepper-container { display: flex; justify-content: space-between; margin-top: 0.5rem; margin-bottom: 1.5rem; padding: 0.75rem 1rem; background: rgba(26, 79, 124, 0.05); border-radius: 8px; border: 1px solid rgba(26, 79, 124, 0.1); }
    .step-item { font-size: 13px; font-weight: 600; color: #6c757d; display: flex; align-items: center; gap: 6px; }
    .step-item.active { color: #0070C0; }
    .step-item.done { color: #28a745; }
    .metric-card { background: #ffffff; border: 1px solid #e0e0e0; border-radius: 10px; padding: 16px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); display: flex; align-items: center; gap: 16px; margin-bottom: 10px; transition: transform 0.2s ease, box-shadow 0.2s ease; }
    .metric-card:hover { transform: translateY(-2px); box-shadow: 0 6px 12px rgba(0,0,0,0.1); }
    .metric-icon { font-size: 26px; padding: 12px; border-radius: 10px; display: flex; align-items: center; justify-content: center; }
    .metric-content .metric-title { font-size: 13px; font-weight: 700; color: #6c757d; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 2px; }
    .metric-content .metric-value { font-size: 26px; font-weight: 800; color: #212529; }
    @media (prefers-color-scheme: dark) {
        .stepper-container { background: rgba(255, 255, 255, 0.05); border-color: rgba(255, 255, 255, 0.1); }
        .metric-card { background: #363945; border-color: #454a59; box-shadow: 0 4px 10px rgba(0,0,0,0.2); }
        .metric-content .metric-title { color: #b3bdc8; }
        .metric-content .metric-value { color: #ffffff; }
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
            st.warning("⚠️ Coluna 'PROTOCOLO' não encontrada.")
    except Exception as e:
        st.error(f"Erro ao aplicar atualização rápida: {e}")
    return df_principal

# ==========================================
# FUNÇÕES MATEMÁTICAS E VRP OR-TOOLS
# ==========================================
def haversine_vectorized(lat1, lon1, lat2, lon2):
    R = 6371.0 
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat/2.0)**2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon/2.0)**2
    c = 2 * np.arcsin(np.sqrt(a))
    return R * c

def roteirizar_equipe_ortools(lista_obras, base_lat, base_lon, cfg):
    """Motor OR-Tools VRP."""
    if not lista_obras: return []
    
    coords = [(base_lat, base_lon)] + [(r['LATITUDE'], r['LONGITUDE']) for r in lista_obras]
    
    distance_matrix = []
    for i in range(len(coords)):
        row = []
        for j in range(len(coords)):
            dist = haversine_vectorized(coords[i][0], coords[i][1], coords[j][0], coords[j][1]) * 1000
            row.append(int(dist))
        distance_matrix.append(row)
        
    num_veiculos = int(cfg['limite_periodos'])
    
    demands = [0]
    capacities = []
    
    if cfg['modo_limite'] == "Quantidade Fixa de Obras":
        for r in lista_obras: demands.append(1) 
        capacities = [int(cfg['obras_por_periodo'])] * num_veiculos
    else:
        for r in lista_obras:
            is_rur = True if ('LOCALIDADE' in r and str(r['LOCALIDADE']).upper() == 'RURAL') or ('TIPO NOTA' in r and str(r['TIPO NOTA']).upper() == 'UNR') else False
            dist_reta_km = haversine_vectorized(base_lat, base_lon, r['LATITUDE'], r['LONGITUDE'])
            tempo_viagem = (dist_reta_km / cfg['velocidade_media_kmh']) * (1.6 if is_rur else 1.0) * 60
            peso_minutos = int(tempo_viagem + (cfg['tempo_medio_obra'] * 60))
            demands.append(peso_minutos)
        max_minutos_dia = int(cfg['horas_por_dia'] * 60)
        capacities = [max_minutos_dia] * num_veiculos

    manager = pywrapcp.RoutingIndexManager(len(distance_matrix), num_veiculos, 0)
    routing = pywrapcp.RoutingModel(manager)

    def distance_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return distance_matrix[from_node][to_node]

    transit_callback_index = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    def demand_callback(from_index):
        from_node = manager.IndexToNode(from_index)
        return demands[from_node]

    demand_callback_index = routing.RegisterUnaryTransitCallback(demand_callback)
    routing.AddDimensionWithVehicleCapacity(
        demand_callback_index,
        0,  
        capacities,  
        True,  
        'Capacity')

    penalty = 10000000 
    for node in range(1, len(distance_matrix)):
        routing.AddDisjunction([manager.NodeToIndex(node)], penalty)

    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    search_parameters.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    search_parameters.time_limit.seconds = 3 
    
    solution = routing.SolveWithParameters(search_parameters)
    
    rotas_por_periodo = []
    if solution:
        for vehicle_id in range(num_veiculos):
            index = routing.Start(vehicle_id)
            rota_atual = []
            while not routing.IsEnd(index):
                node_index = manager.IndexToNode(index)
                if node_index != 0:
                    rota_atual.append(lista_obras[node_index - 1])
                index = solution.Value(routing.NextVar(index))
            if len(rota_atual) > 0:
                rotas_por_periodo.append(rota_atual)
    
    return rotas_por_periodo

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

@st.cache_data(show_spinner=False)
def obter_coordenadas_municipio_cached(municipio):
    if not municipio or pd.isna(municipio) or str(municipio).strip() == "": return np.nan, np.nan
    try:
        url = f"https://nominatim.openstreetmap.org/search?q={str(municipio).strip()},+Maranhão,+Brasil&format=json&limit=1"
        r = http_session.get(url, headers={"User-Agent": "GeradorRotasOperacional/9.0"}, timeout=5)
        if r.status_code == 200 and len(r.json()) > 0: return float(r.json()[0]['lat']), float(r.json()[0]['lon'])
    except: pass
    return np.nan, np.nan

def obter_rota_ruas(lat1, lon1, lat2, lon2, url_osrm_base, vel_fallback_kmh=30):
    try:
        url = f"{url_osrm_base}/route/v1/driving/{lon1:.6f},{lat1:.6f};{lon2:.6f},{lat2:.6f}?overview=full&geometries=geojson"
        r = http_session.get(url, timeout=5)
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
    for col in ['STATUS LIST', 'INICIO AVARIA', 'STATUS ATUAL (LEVANTAMENTO)', 'DESCRICAO']:
        if col in df_export.columns: df_export = df_export.drop(columns=[col])

    if colunas_originais:
        cols_atuais = df_export.columns.tolist()
        cols_originais_validas = [c for c in colunas_originais if c in cols_atuais]
        cols_novas_geradas = [c for c in cols_atuais if c not in colunas_originais]
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
            if any(x in col_name_upper for x in ['NOME', 'CLIENTE', 'ENDEREÇO', 'INFORMAÇ']):
                ws.column_dimensions[col_letter].width = 45.0
            elif any(x in col_name_upper for x in ['PROTOCOLO', 'MUNICIPIO', 'BASE', 'LOCALIDADE']):
                ws.column_dimensions[col_letter].width = 25.0
            else:
                ws.column_dimensions[col_letter].width = 18.0
                
            if col_name_upper in ['ORDEM', 'SEMANA', 'DIA', 'PERIODO', 'DISTANCIA_PONTO_ANTERIOR_KM', 'DISTANCIA_PROXIMO_PONTO_KM', 'TEMPO_VIAGEM_MINUTOS', 'PRIORIDADE']:
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
                    try: ws.cell(row=row_idx, column=prio_target_idx).font = red_font
                    except: pass
                    
    return buf_xl.getvalue()

def gerar_kml_agrupado(df_rota, bases_records, doc_name, cols_exibir, lista_todas_bases=None, tipo_periodo="Dia"):
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
                for _, r in df_dia.iterrows():
                    lon, lat = str(r['LONGITUDE']).replace(',','.'), str(r['LATITUDE']).replace(',','.')
                    
                    if r.get('PROTOCOLO') == 'RETORNO_BASE':
                        nome_ponto, style_url = "🏠 FIM DO DIA - RETORNO", "#icon-green"
                        popup_html = "<b>RETORNO À BASE DE ORIGEM</b>"
                    elif r.get('PROTOCOLO') == 'PAUSA_ALMOCO':
                        nome_ponto, style_url = "🍔 ALMOÇO DA EQUIPE", "#icon-yellow"
                        popup_html = "<b>PAUSA PROGRAMADA PARA REFEIÇÃO (1h)</b>"
                    else:
                        pop_header_bg = "#d9534f" if r.get('PRIORIDADE') == "Sim" else "#0070C0"
                        pop_prio_txt = "🚨 OBRA PRIORITÁRIA" if r.get('PRIORIDADE') == "Sim" else "📍 Atendimento Padrão"
                        
                        dist_prox = r.get('DISTANCIA_PROXIMO_PONTO_KM', 0.0)
                        
                        extra_rows = ""
                        for c in cols_exibir:
                            if c in r and c.upper() != 'PROTOCOLO': 
                                extra_rows += f"<tr><td style='padding:4px 8px; font-weight:bold; color:#444; border-bottom:1px solid #eee;'>{html.escape(str(c))}:</td><td style='padding:4px 8px; color:#222; border-bottom:1px solid #eee;'>{html.escape(str(r[c]))}</td></tr>"

                        popup_html = f"""
                        <div style="font-family: Arial, sans-serif; width:300px; border-radius:6px; overflow:hidden; border:1px solid #ccc; background:#fff;">
                            <div style="background:{pop_header_bg}; color:white; padding:10px; font-size:14px; font-weight:bold; text-align:center;">
                                {pop_prio_txt}
                            </div>
                            <div style="padding:10px; background:#fafafa; font-size:13px;">
                                <table style="width:100%; border-collapse:collapse; text-align:left;">
                                    <tr><td style="padding:4px 8px; font-weight:bold; color:#444; border-bottom:1px solid #eee; width:40%;">Protocolo:</td><td style="padding:4px 8px; color:#222; border-bottom:1px solid #eee;">{html.escape(str(r.get('PROTOCOLO', 'N/A')))}</td></tr>
                                    <tr><td style="padding:4px 8px; font-weight:bold; color:#444; border-bottom:1px solid #eee;">Ordem:</td><td style="padding:4px 8px; color:#222; border-bottom:1px solid #eee;">{r.get('ORDEM', 0)} ({tipo_periodo} {r.get('PERIODO', 0)})</td></tr>
                                    <tr><td style="padding:4px 8px; font-weight:bold; color:#444; border-bottom:1px solid #eee;">Distância Ant.:</td><td style="padding:4px 8px; color:#222; border-bottom:1px solid #eee;">{r.get('DISTANCIA_PONTO_ANTERIOR_KM', 0)} KM</td></tr>
                                    <tr><td style="padding:4px 8px; font-weight:bold; color:#444; border-bottom:1px solid #eee;">Distância Próx.:</td><td style="padding:4px 8px; color:#222; border-bottom:1px solid #eee;">{dist_prox} KM</td></tr>
                                    <tr><td style="padding:4px 8px; font-weight:bold; color:#444; border-bottom:1px solid #eee;">Tempo Est.:</td><td style="padding:4px 8px; color:#222; border-bottom:1px solid #eee;">{r.get('TEMPO_VIAGEM_MINUTOS', 0)} Min</td></tr>
                                    {extra_rows}
                                </table>
                            </div>
                        </div>
                        """
                        
                        tag_prio = "[PRIORIDADE] " if r.get('PRIORIDADE') == "Sim" else ""
                        nome_ponto = f"{tag_prio}[{r.get('ORDEM', 0)}] Prot: {html.escape(str(r.get('PROTOCOLO', 'N/A')))}"
                        style_url = "#icon-red" if r.get('PRIORIDADE') == "Sim" else "#icon-blue"

                    kml += f'        <Placemark><name>{nome_ponto}</name><description><![CDATA[{popup_html}]]></description><styleUrl>{style_url}</styleUrl><Point><coordinates>{lon},{lat},0</coordinates></Point></Placemark>\n'
                    
                    if isinstance(r.get('ROTA_GEOMETRIA'), list):
                        coords_linha_kml += "".join([f"          {pt_lon},{pt_lat},0\n" for pt_lon, pt_lat in r['ROTA_GEOMETRIA']])
                    else:
                        coords_linha_kml += f"          {lon},{lat},0\n"

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

    status_exec = st.session_state.vrp_status
    is_done = st.session_state.roteamento_concluido
    
    if is_done:
        if st.session_state.df_routed.empty:
            st.error("🚨 Nenhuma rota pôde ser gerada! Isso geralmente ocorre porque o limite de obras, km ou carga horária é muito pequeno para alocar até mesmo uma única demanda (ou as obras estão distantes demais da equipe).")
            if st.button("⬅️ Voltar e Ajustar Limites"):
                limpar_roteirizador()
            return

    s1_class = "step-item done" if (status_exec != "IDLE" or is_done) else "step-item active"
    s2_class = "step-item done" if (status_exec != "IDLE" or is_done) else "step-item active"
    s3_class = "step-item active" if status_exec != "IDLE" else ("step-item done" if is_done else "step-item")
    s4_class = "step-item active" if is_done else "step-item"
    
    st.markdown(f"""
    <div class="stepper-container">
        <div class="{s1_class}">📁 1. Upload de Dados</div>
        <div class="{s2_class}">⚙️ 2. Filtros e Configuração</div>
        <div class="{s3_class}">🚀 3. Roteirização Inteligente (OR-Tools)</div>
        <div class="{s4_class}">🎯 4. Resultados e Mapas</div>
    </div>
    """, unsafe_allow_html=True)

    is_locked = status_exec != "IDLE" or is_done
    
    with st.sidebar:
        st.markdown("### ⚙️ Gestão de Esforço Diário")
        tipo_periodo = st.radio("Como agrupar o roteiro?", ["Dia", "Semana"], horizontal=True, disabled=is_locked)
        modo_limite = st.radio("Critério limitador da equipe:", ["Quantidade Fixa de Obras", "Carga Horária (Tempo Real)"], disabled=is_locked)
        
        limite_km_diario = st.slider(f"Limite Máximo de KM por {tipo_periodo}", min_value=0, max_value=500, value=500, step=5, disabled=is_locked)
        
        obras_por_periodo = 10
        horas_por_dia = 8.0
        tempo_medio_obra = 1.5
        velocidade_media_kmh = 30.0
        
        if modo_limite == "Quantidade Fixa de Obras":
            obras_por_periodo = st.number_input(f"Máximo de Obras por {tipo_periodo}", min_value=1, value=10, step=1, disabled=is_locked)
            limite_periodos = st.number_input(f"Limite total de {tipo_periodo}s a roteirizar", min_value=1, value=5, step=1, disabled=is_locked)
        else:
            horas_por_dia = st.number_input(f"Horas disponíveis por {tipo_periodo}", min_value=1.0, value=8.0, step=0.5, disabled=is_locked)
            tempo_medio_obra = st.number_input("Tempo médio execução/obra (Horas)", min_value=0.1, value=1.5, step=0.1, disabled=is_locked)
            velocidade_media_kmh = st.number_input("Velocidade Estimada (km/h)", min_value=10.0, value=30.0, step=5.0, disabled=is_locked)
            limite_periodos = st.number_input(f"Limite total de {tipo_periodo}s a roteirizar", min_value=1, value=5, step=1, disabled=is_locked)
            
        st.markdown("---")
        st.markdown("### 📡 Conexão de Roteamento")
        st.caption("Implementação 2A: Para desempenho máximo, suba um OSRM Local via Docker e aponte a URL abaixo.")
        url_osrm_base = st.text_input("Endpoint OSRM:", value="http://router.project-osrm.org", disabled=is_locked)
        
        st.markdown("---")
        timer_placeholder = st.empty()

    # -------------------------------------------------------------
    # 1. TELA DE RESULTADOS (Após Roteirização Finalizada)
    # -------------------------------------------------------------
    if is_done and not st.session_state.df_routed.empty:
        st.markdown("## 🎯 Resultados da Roteirização Corporativa")
        st.info("Dê um **duplo clique** nas células abaixo para alterar o responsável ou a ordem das obras.")
        
        # Re-garante a atualização da coluna DISTANCIA_PROXIMO_PONTO_KM caso o usuário edite a ordem manualmente
        st.session_state.df_routed['DISTANCIA_PROXIMO_PONTO_KM'] = st.session_state.df_routed.groupby(['BASE_ATRIBUIDA', 'PERIODO'])['DISTANCIA_PONTO_ANTERIOR_KM'].shift(-1).fillna(0.0)

        df_editado_ui = st.data_editor(
            st.session_state.df_routed, use_container_width=True,
            column_config={ 
                "ROTA_GEOMETRIA": None, 
                "LATITUDE": st.column_config.NumberColumn(disabled=True), 
                "LONGITUDE": st.column_config.NumberColumn(disabled=True),
                "DISTANCIA_PONTO_ANTERIOR_KM": st.column_config.NumberColumn(disabled=True),
                "DISTANCIA_PROXIMO_PONTO_KM": st.column_config.NumberColumn(disabled=True),
                "TEMPO_VIAGEM_MINUTOS": st.column_config.NumberColumn(disabled=True)
            }
        )
        
        df_routed = df_editado_ui.copy()
        bases_records = st.session_state.bases_records
        tipo_periodo = st.session_state.tipo_periodo
        colunas_exibir = st.session_state.colunas_exibir
        df_real_tasks = df_routed[~df_routed['PROTOCOLO'].isin(['RETORNO_BASE', 'PAUSA_ALMOCO'])]
        
        tot_obras = len(df_real_tasks)
        tot_equipes = df_routed['BASE_ATRIBUIDA'].nunique()
        tot_km = f"{df_routed['DISTANCIA_PONTO_ANTERIOR_KM'].sum():.1f} km"
        tot_prio = len(df_real_tasks[df_real_tasks['PRIORIDADE'] == 'Sim']) if 'PRIORIDADE' in df_real_tasks else 0

        c_m1, c_m2, c_m3, c_m4 = st.columns(4)
        c_m1.markdown(f'<div class="metric-card" style="border-left: 5px solid #3b82f6;"><div class="metric-icon" style="background: rgba(59, 130, 246, 0.15);">📌</div><div class="metric-content"><div class="metric-title">Obras Roteirizadas</div><div class="metric-value">{tot_obras}</div></div></div>', unsafe_allow_html=True)
        c_m2.markdown(f'<div class="metric-card" style="border-left: 5px solid #8b5cf6;"><div class="metric-icon" style="background: rgba(139, 92, 246, 0.15);">👥</div><div class="metric-content"><div class="metric-title">Equipes em Campo</div><div class="metric-value">{tot_equipes}</div></div></div>', unsafe_allow_html=True)
        c_m3.markdown(f'<div class="metric-card" style="border-left: 5px solid #10b981;"><div class="metric-icon" style="background: rgba(16, 185, 129, 0.15);">🛣️</div><div class="metric-content"><div class="metric-title">KM Total Projetado</div><div class="metric-value">{tot_km}</div></div></div>', unsafe_allow_html=True)
        c_m4.markdown(f'<div class="metric-card" style="border-left: 5px solid #ef4444;"><div class="metric-icon" style="background: rgba(239, 68, 68, 0.15);">🚨</div><div class="metric-content"><div class="metric-title">Prioridades</div><div class="metric-value">{tot_prio}</div></div></div>', unsafe_allow_html=True)

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
        
        # === MAPA FOLIUM COM DISTÂNCIA ANTERIOR E DISTÂNCIA PRÓXIMO ===
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
                    
                    pop_header_bg = "#d9534f" if r.get('PRIORIDADE') == "Sim" else "#0070C0"
                    pop_prio_txt = "🚨 OBRA PRIORITÁRIA" if r.get('PRIORIDADE') == "Sim" else "📍 Atendimento Padrão"
                    
                    dist_prox = r.get('DISTANCIA_PROXIMO_PONTO_KM', 0.0)
                    
                    extra_rows = ""
                    for c in colunas_exibir:
                        if c in r and c.upper() != 'PROTOCOLO': 
                            extra_rows += f"<tr><td style='padding:3px 6px; font-weight:bold; color:#555;'>{c}:</td><td style='padding:3px 6px; color:#333;'>{r[c]}</td></tr>"

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
                                <tr><td style="padding:3px 6px; font-weight:bold; color:#555;">Distância Próx.:</td><td style="padding:3px 6px; color:#333;">{dist_prox} KM</td></tr>
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
            zip_xl.writestr(f"Roteiro_Geral_{data_atual}.xlsx", gerar_excel_bytes(df_routed, st.session_state.col_prioridade, st.session_state.colunas_originais))
            planilhas_geradas = [f"Roteiro_Geral_{data_atual}.xlsx"]

            resumo_levantadores = []
            for base in df_routed['BASE_ATRIBUIDA'].unique():
                df_base = df_routed[df_routed['BASE_ATRIBUIDA'] == base]
                df_base_real = df_base[~df_base['PROTOCOLO'].isin(['RETORNO_BASE', 'PAUSA_ALMOCO'])]
                base_ref = next((b for b in bases_records if b['LEVANTADOR'] == base), None)
                tipo_eq = base_ref.get('TIPO_EQUIPE', 'PRINCIPAL') if base_ref else 'DESCONHECIDO'
                qtd_comum = len(df_base_real[df_base_real['PRIORIDADE'] == 'Não']) if 'PRIORIDADE' in df_base_real.columns else len(df_base_real)
                qtd_prio = len(df_base_real[df_base_real['PRIORIDADE'] == 'Sim']) if 'PRIORIDADE' in df_base_real.columns else 0
                
                resumo_levantadores.append({
                    'LEVANTADOR': base, 'TIPO EQUIPE': tipo_eq, 'OBRAS COMUNS': qtd_comum,
                    'OBRAS PRIORITARIAS': qtd_prio, 'TOTAL OBRAS': qtd_comum + qtd_prio,
                    'KM TOTAL PREVISTO': round(df_base['DISTANCIA_PONTO_ANTERIOR_KM'].sum(), 2)
                })

            buf_resumo_lev = io.BytesIO()
            with pd.ExcelWriter(buf_resumo_lev, engine='openpyxl') as writer:
                pd.DataFrame(resumo_levantadores).to_excel(writer, index=False, sheet_name='Resumo')
            zip_xl.writestr(f"Resumo_Levantadores_{data_atual}.xlsx", buf_resumo_lev.getvalue())
            
            for base_nome in df_routed['BASE_ATRIBUIDA'].unique():
                df_lev = df_routed[df_routed['BASE_ATRIBUIDA'] == base_nome].copy()
                nome_seguro = re.sub(r'[^A-Za-z0-9_]', '', str(base_nome).replace(" ", "_"))
                zip_xl.writestr(f"Roteiro_{nome_seguro}_{data_atual}.xlsx", gerar_excel_bytes(df_lev, st.session_state.col_prioridade, st.session_state.colunas_originais))
                    
        buf_zip_kml = io.BytesIO()
        with zipfile.ZipFile(buf_zip_kml, 'w', zipfile.ZIP_DEFLATED) as zip_kml:
            lista_bases_geral = df_routed['BASE_ATRIBUIDA'].unique().tolist()
            zip_kml.writestr(f"Rota_Geral_{data_atual}.kml", gerar_kml_agrupado(df_routed, bases_records, f"Rota_Geral_{data_atual}", st.session_state.colunas_exibir, lista_bases_geral, tipo_periodo).encode('utf-8'))
            for base_nome in df_routed['BASE_ATRIBUIDA'].unique():
                df_lev = df_routed[df_routed['BASE_ATRIBUIDA'] == base_nome].copy()
                nome_seguro = re.sub(r'[^A-Za-z0-9_]', '', str(base_nome).replace(" ", "_"))
                zip_kml.writestr(f"Rota_{nome_seguro}_{data_atual}.kml", gerar_kml_agrupado(df_lev, bases_records, f"Rota_{nome_seguro}", st.session_state.colunas_exibir, lista_bases_geral, tipo_periodo).encode('utf-8'))

        col_b1, col_b2, col_b3 = st.columns([1, 1, 1])
        col_b1.download_button("🌐 1. Planilhas Roteirizadas (ZIP)", data=buf_zip_xl.getvalue(), file_name=f"Dados_Estruturados_Roteiro_{data_atual}.zip", mime="application/zip", use_container_width=True)
        col_b2.download_button("🗺️ 2. Baixar Mapas (KML ZIP)", data=buf_zip_kml.getvalue(), file_name=f"Mapas_KML_{data_atual}.zip", mime="application/zip", use_container_width=True)
        if col_b3.button("🧹 Zerar Roteirizador", type="primary", use_container_width=True): limpar_roteirizador()
        return 

    # -------------------------------------------------------------
    # 2. MÁQUINA DE ESTADOS - ROTEAMENTO ASSÍNCRONO POR LOTES
    # -------------------------------------------------------------
    if status_exec in ["RUNNING"]:
        st.markdown("## 🚀 Execução do Motor de Inteligência (OR-Tools VRP)")
        st.markdown("O servidor logístico de alta performance está roteando as equipes em lote.")
        
        if st.button("⏹️ Abortar Execução", use_container_width=True): limpar_roteirizador()
            
        state = st.session_state.vrp_state
        cfg = state['config']
        total_equipes = len(state['b_names'])
        progresso = min(state['b_idx'] / total_equipes, 1.0) if total_equipes > 0 else 1.0
        
        st.progress(progresso)
        status_text = st.empty()
        
        agora = time.time()
        if 'last_time' not in state: state['last_time'] = agora
        if 'tempo_processamento' not in state: state['tempo_processamento'] = 0.0
        
        state['tempo_processamento'] += (agora - state['last_time'])
        state['last_time'] = agora

        with timer_placeholder.container():
            if state['b_idx'] > 0:
                avg = state['tempo_processamento'] / state['b_idx']
                restantes = total_equipes - state['b_idx']
                est_rem = avg * restantes
                m, s = divmod(int(est_rem), 60)
                h, m = divmod(m, 60)
                time_str = f"{h:02d}h {m:02d}m {s:02d}s" if h > 0 else f"{m:02d}m {s:02d}s"
                
                html_timer = f"""
                <style>
                @keyframes flip-glass {{
                    0% {{ transform: rotate(0deg); }}
                    40% {{ transform: rotate(180deg); }}
                    50% {{ transform: rotate(180deg); }}
                    90% {{ transform: rotate(360deg); }}
                    100% {{ transform: rotate(360deg); }}
                }}
                .hourglass-anim {{ display: inline-block; animation: flip-glass 2.5s ease-in-out infinite; }}
                .timer-alert {{ padding: 0.75rem 1rem; border-radius: 0.5rem; background-color: rgba(46, 123, 50, 0.15); color: #176B2C; border: 1px solid rgba(46, 123, 50, 0.3); display: flex; align-items: center; font-family: sans-serif; }}
                @media (prefers-color-scheme: dark) {{ .timer-alert {{ background-color: rgba(60, 179, 113, 0.15); color: #66bb6a; border: 1px solid rgba(60, 179, 113, 0.3); }} }}
                </style>
                <div class="timer-alert">
                    <span class="hourglass-anim" style="font-size:1.5rem; margin-right:12px;">⏳</span> 
                    <strong style="font-size:1.2rem; letter-spacing: 0.5px;">{time_str}</strong>
                </div>
                """
                st.markdown("### ⏱️ Tempo Restante")
                st.markdown(html_timer, unsafe_allow_html=True)
            else:
                st.markdown("### ⏱️ Tempo Restante")
                st.markdown("""
                <style>
                @keyframes pulse-text { 0% { opacity: 0.4; } 50% { opacity: 1; } 100% { opacity: 0.4; } }
                .calculating-alert { padding: 0.75rem 1rem; border-radius: 0.5rem; background-color: rgba(26, 79, 124, 0.15); color: #1A4F7C; border: 1px solid rgba(26, 79, 124, 0.3); font-weight: 600; display: flex; align-items: center; }
                @media (prefers-color-scheme: dark) { .calculating-alert { color: #64B5F6; } }
                .spin-icon { display: inline-block; animation: pulse-text 1.5s infinite; }
                </style>
                <div class="calculating-alert">
                    <span class="spin-icon" style="font-size:1.2rem; margin-right:10px;">🔄</span> 
                    <span style="animation: pulse-text 1.5s infinite;">Calculando estimativa...</span>
                </div>
                """, unsafe_allow_html=True)

        df_todas_bases_ativas = pd.DataFrame(st.session_state.bases_records)
        
        if state['b_idx'] >= len(state['b_names']):
            status_text.success("✅ Otimização Concluída!")
            
            df_final_route = pd.DataFrame(state['routed_data'])
            if not df_final_route.empty:
                df_final_route['DISTANCIA_PROXIMO_PONTO_KM'] = df_final_route.groupby(['BASE_ATRIBUIDA', 'PERIODO'])['DISTANCIA_PONTO_ANTERIOR_KM'].shift(-1).fillna(0.0)
            
            st.session_state.df_routed = df_final_route
            st.session_state.roteamento_concluido = True
            st.session_state.vrp_status = "IDLE"
            st.rerun()
        else:
            b_name = state['b_names'][state['b_idx']]
            status_text.info(f"🧠 A inteligência artificial (OR-Tools) está resolvendo as rotas de **{b_name}**... ({state['b_idx'] + 1}/{total_equipes})")
            
            base_ref = df_todas_bases_ativas[df_todas_bases_ativas['LEVANTADOR'] == b_name].iloc[0]
            if pd.isna(base_ref.get('LATITUDE')):
                state['b_idx'] += 1
                st.rerun()
                
            base_lat, base_lon = float(base_ref['LATITUDE']), float(base_ref['LONGITUDE'])
            
            unvisited = state['unvisited']
            obras_equipe = unvisited[unvisited['BASE_ATRIBUIDA'] == b_name].to_dict('records')
            
            if obras_equipe:
                rotas_resolvidas = roteirizar_equipe_ortools(obras_equipe, base_lat, base_lon, cfg)
                
                ordem_global = 1
                for idx_periodo, rota_dia in enumerate(rotas_resolvidas):
                    periodo = idx_periodo + 1
                    lat_atual, lon_atual = base_lat, base_lon
                    
                    for r_idx, obra in enumerate(rota_dia):
                        rota_geom, dur_sec = obter_rota_ruas(lat_atual, lon_atual, obra['LATITUDE'], obra['LONGITUDE'], url_osrm_base, cfg['velocidade_media_kmh'])
                        
                        obra['ORDEM'] = ordem_global
                        obra['SEMANA'] = periodo if cfg['tipo_periodo'] == "Semana" else 1
                        obra['DIA'] = periodo if cfg['tipo_periodo'] == "Dia" else 1
                        obra['PERIODO'] = periodo
                        obra['DISTANCIA_PONTO_ANTERIOR_KM'] = round(haversine_vectorized(lat_atual, lon_atual, obra['LATITUDE'], obra['LONGITUDE']), 2)
                        obra['TEMPO_VIAGEM_MINUTOS'] = round(dur_sec / 60.0, 1)
                        obra['ROTA_GEOMETRIA'] = rota_geom
                        
                        state['routed_data'].append(obra)
                        lat_atual, lon_atual = obra['LATITUDE'], obra['LONGITUDE']
                        ordem_global += 1
                        
                    rota_retorno, dur_ret_seg = obter_rota_ruas(lat_atual, lon_atual, base_lat, base_lon, url_osrm_base, cfg['velocidade_media_kmh'])
                    dist_retorno = haversine_vectorized(lat_atual, lon_atual, base_lat, base_lon)
                    state['routed_data'].append({
                        'PROTOCOLO': 'RETORNO_BASE', 'NOME DO SOLICITANTE': 'BASE_RETORNO', 'LATITUDE': base_lat, 'LONGITUDE': base_lon,
                        'BASE_ATRIBUIDA': b_name, 'ORDEM': ordem_global, 'SEMANA': periodo if cfg['tipo_periodo'] == "Semana" else 1,
                        'DIA': periodo if cfg['tipo_periodo'] == "Dia" else 1, 'PERIODO': periodo,
                        'DISTANCIA_PONTO_ANTERIOR_KM': round(dist_retorno, 2), 'TEMPO_VIAGEM_MINUTOS': round(dur_ret_seg / 60.0, 1),
                        'ROTA_GEOMETRIA': rota_retorno, 'PRIORIDADE': 'Não'
                    })
                    ordem_global += 1

            state['b_idx'] += 1
            st.rerun()
        return 

    # -------------------------------------------------------------
    # 3. TELA DE CONFIGURAÇÃO INICIAL (Uploads e Ajustes)
    # -------------------------------------------------------------
    col_up_1, col_up_2 = st.columns(2)

    with col_up_1:
        st.markdown("### 👥 1. Levantadores Principais")
        df_bases = pd.DataFrame()

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
                            with st.spinner("🌍 Mapeando coordenadas (Satélite)..."):
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
                else:
                    st.error("❌ A planilha de Equipes não possui a coluna 'LEVANTADOR', 'COLABORADOR' ou 'NOME'.")
            except Exception as e:
                st.error(f"Erro ao ler a planilha: {e}")

        st.markdown("##### Regra de Atribuição Territorial")
        tipo_atribuicao = st.radio("Regra", ["Por Municípios Atendidos (Lê texto da planilha)", "Por Proximidade Geográfica das Coordenadas (Ignora texto)", "Clusterização Inteligente por IA (K-Means)"], index=0, label_visibility="collapsed")

    with col_up_2:
        st.markdown("### 📁 2. Upload de Demandas (Obras)")
        
        with st.container(border=True):
            task_files = st.file_uploader("1️⃣ Base Principal (Planilha de Obras)", type=["xlsx", "xls"], accept_multiple_files=True)
        
        st.markdown("##### 🔄 Atualização Rápida de Status (Opcional)")
        with st.container(border=True):
            status_file = st.file_uploader("2️⃣ Planilha Atualizada SharePoint", type=["xlsx", "xls"])
        
        df_status_upload = pd.DataFrame()
        coluna_status_selecionada = None
        
        if status_file:
            try:
                df_status_upload = pd.read_excel(status_file)
                cols_status = df_status_upload.columns.tolist()
                def_idx = 4 if len(cols_status) >= 5 else 0
                coluna_status_selecionada = st.selectbox("📌 Qual coluna contém o Status?", cols_status, index=def_idx)
            except Exception as e:
                st.error(f"Erro ao ler status: {e}")
        
        st.markdown("##### 🧑‍🤝‍🧑 3. Equipes de Apoio (Temporários)")
        
        with st.container(border=True):
            temp_bases_files = st.file_uploader("Suba a(s) planilha(s) de Apoio", type=["xlsx", "xls"], accept_multiple_files=True)
        
        df_bases_temp = pd.DataFrame()
        if temp_bases_files:
            try:
                dfs_temp = []
                for f in temp_bases_files:
                    df_t = pd.read_excel(f)
                    df_t.columns = normalize_cols(df_t.columns)
                    if 'LEVANTADOR' not in df_t.columns:
                        for p_nome in ['NOME', 'TECNICO', 'EQUIPE']:
                            if p_nome in df_t.columns:
                                df_t = df_t.rename(columns={p_nome: 'LEVANTADOR'})
                                break
                    dfs_temp.append(df_t)
                df_bases_temp_full = pd.concat(dfs_temp, ignore_index=True)
                
                if 'LEVANTADOR' in df_bases_temp_full.columns:
                    opcoes_levs_temp = sorted([str(x) for x in df_bases_temp_full['LEVANTADOR'].dropna().unique().tolist() if str(x).upper().strip() != 'SEM LEVANTADOR'])
                    levs_temp_selecionados = st.multiselect("Selecione as Equipes:", opcoes_levs_temp, key="ms_temp")
                    
                    if levs_temp_selecionados:
                        df_bases_temp = df_bases_temp_full[df_bases_temp_full['LEVANTADOR'].isin(levs_temp_selecionados)].copy()
                        if 'RESIDENCIA' in df_bases_temp.columns:
                            muns_unicos_temp = df_bases_temp['RESIDENCIA'].dropna().unique()
                            mapa_coords_temp = {}
                            with st.spinner("🌍 Mapeando bases temporárias..."):
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
                st.error(f"Erro: {e}")

        if not task_files: st.info("Aguardando upload."); return

        try:
            dfs = []
            for f in task_files:
                df_temp = pd.read_excel(f)
                if len(dfs) == 0: st.session_state.colunas_originais = df_temp.columns.tolist()
                df_temp.columns = normalize_cols(df_temp.columns)
                dfs.append(df_temp)
            df_tasks = pd.concat(dfs, ignore_index=True)
        except Exception as e:
            st.error(f"Erro ao unificar: {e}"); return

        if not df_status_upload.empty and coluna_status_selecionada:
            df_tasks = atualizar_status_via_df(df_tasks, df_status_upload, coluna_status_selecionada)

    if 'LATITUDE' not in df_tasks.columns or 'LONGITUDE' not in df_tasks.columns:
        st.error("❌ A planilha precisa ter LATITUDE e LONGITUDE."); return

    st.markdown("---")
    
    if 'STATUS LIST' in df_tasks.columns:
        df_tasks['STATUS_LIMPO'] = df_tasks['STATUS LIST'].astype(str).str.strip().str.upper()
        status_unicos = sorted(df_tasks['STATUS_LIMPO'].unique().tolist())
        padroes_ativos = [s for s in status_unicos if s in STATUS_PADRAO]
        
        status_selecionados = st.multiselect("📌 Status na Roteirização:", options=status_unicos, default=padroes_ativos)
        if not status_selecionados: st.warning("Selecione um status."); return
            
        df_tasks = df_tasks[df_tasks['STATUS_LIMPO'].isin(status_selecionados)].drop(columns=['STATUS_LIMPO'])

    total_orig = len(df_tasks)
    df_tasks['LATITUDE'] = pd.to_numeric(df_tasks['LATITUDE'].astype(str).str.replace(',', '.'), errors='coerce')
    df_tasks['LONGITUDE'] = pd.to_numeric(df_tasks['LONGITUDE'].astype(str).str.replace(',', '.'), errors='coerce')
    df_tasks = df_tasks.dropna(subset=['LATITUDE', 'LONGITUDE'])
    df_tasks = df_tasks[(df_tasks['LATITUDE'] != 0.0) & (df_tasks['LONGITUDE'] != 0.0)]
    
    for col_nome in ['NOME', 'NOME DO SOLICITANTE', 'CLIENTE']:
        if col_nome in df_tasks.columns:
            df_tasks = df_tasks.dropna(subset=[col_nome])
            df_tasks = df_tasks[df_tasks[col_nome].astype(str).str.strip() != '']
            
    if 'STATUS SAP' in df_tasks.columns: df_tasks = df_tasks[~df_tasks['STATUS SAP'].astype(str).str.strip().str.upper().isin(['CANC', 'FINL'])]

    if 'TIPO NOTA' in df_tasks.columns: df_tasks['PRIORIDADE'] = df_tasks['TIPO NOTA'].apply(lambda x: 'Sim' if str(x).strip().upper() in TIPOS_PRIORITARIOS else 'Não')
    else: df_tasks['PRIORIDADE'] = 'Não'

    if total_orig - len(df_tasks) > 0: st.warning(f"⚠️ {total_orig - len(df_tasks)} obras ignoradas (sem coordenadas). Restam **{len(df_tasks)} válidas.**")
    if df_tasks.empty: return

    # === ALOCAÇÃO TERRITORIAL ===
    df_tasks_alocadas = pd.DataFrame()
    bases_principais_records = df_bases.to_dict('records') if not df_bases.empty else []
    bases_temporarias_records = df_bases_temp.to_dict('records') if not df_bases_temp.empty else []
    todas_bases_records = bases_principais_records + bases_temporarias_records
    
    if len(todas_bases_records) > 0:
        df_tasks['BASE_ATRIBUIDA'] = "NÃO ALOCADO"
        df_prio = df_tasks[df_tasks['PRIORIDADE'] == 'Sim'].copy()
        df_comum = df_tasks[df_tasks['PRIORIDADE'] == 'Não'].copy()
        
        if tipo_atribuicao == "Clusterização Inteligente por IA (K-Means)":
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

        if df_tasks_alocadas.empty: st.error("Nenhuma obra encontrada para as equipes."); return
        if not df_unallocated.empty: st.warning(f"⚠️ {len(df_unallocated)} obras carregadas sem Levantador.")
            
        bases_records = todas_bases_records 

    # === CONFIGURAÇÃO DE EXIBIÇÃO ===
    if not df_tasks_alocadas.empty:
        with st.expander("🛠️ 4. Configuração de Roteirização (Filtros)", expanded=True):
            c_ex1, c_ex2 = st.columns(2)
            if 'TIPO NOTA' in df_tasks_alocadas.columns:
                tipos_nota_unicos = sorted(df_tasks_alocadas['TIPO NOTA'].astype(str).dropna().unique().tolist())
                tipos_selecionados = c_ex1.multiselect("🏷️ Filtrar TIPO DE NOTA (Opcional):", tipos_nota_unicos, default=tipos_nota_unicos)
                if not tipos_selecionados: st.warning("Selecione um Tipo de Nota."); return
                df_tasks_alocadas = df_tasks_alocadas[df_tasks_alocadas['TIPO NOTA'].astype(str).isin(tipos_selecionados)]

            todas_cols = df_tasks_alocadas.columns.tolist()
            cols_desejadas = ['PROTOCOLO', 'NOME', 'ENDEREÇO', 'MUNICIPIO', 'LATITUDE', 'LONGITUDE', 'TIPO NOTA']
            cols_padrao = [c for c in normalize_cols(cols_desejadas) if c in todas_cols]
            colunas_exibir = c_ex1.multiselect("Colunas Visíveis", todas_cols, default=cols_padrao)
            c_ex2.info(f"⚡ **Prioridade Ativa:** Obras {', '.join(TIPOS_PRIORITARIOS)} recebem pino vermelho.")
            col_prioridade = "TIPO NOTA"

    if st.button("🚀 Iniciar Inteligência de Roteirização (OR-Tools)", type="primary", use_container_width=True):
        if df_tasks_alocadas.empty: st.error("Selecione equipes válidas."); return
        if 'TIPO NOTA' in df_tasks_alocadas.columns and 'TIPO NOTA' not in colunas_exibir: colunas_exibir.append('TIPO NOTA')

        st.session_state.bases_records = bases_records
        st.session_state.tipo_periodo = tipo_periodo
        st.session_state.colunas_exibir = colunas_exibir
        st.session_state.col_prioridade = col_prioridade
        
        st.session_state.vrp_state = {
            'config': {
                'limite_periodos': limite_periodos, 'modo_limite': modo_limite, 'velocidade_media_kmh': velocidade_media_kmh,
                'tempo_medio_obra': tempo_medio_obra, 'horas_por_dia': horas_por_dia, 'limite_km_diario': limite_km_diario,
                'obras_por_periodo': obras_por_periodo, 'tipo_periodo': tipo_periodo
            },
            'b_names': list(set([b['LEVANTADOR'] for b in bases_records])),
            'b_idx': 0, 'unvisited': df_tasks_alocadas.copy(), 'routed_data': [],
        }
        st.session_state.vrp_status = "RUNNING"
        st.rerun()

if __name__ == "__main__":
    view_roteirizador()
