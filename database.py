import streamlit as st
import pandas as pd
import sqlite3
import os
import hashlib
import numpy as np

# =============================================================================
# CONSTANTES GLOBAIS
# =============================================================================
DB_PATH = 'controle_torre_nip.db'
SEM_LEVANTADOR = "SEM LEVANTADOR"
STATUS_PRODUTIVIDADE = ["PENDENTE", "EM ANDAMENTO", "AGUARDANDO", "EM LEVANTAMENTO", "Em levantamento"]

# =============================================================================
# FUNÇÕES DE BANCO DE DADOS E AUTENTICAÇÃO
# =============================================================================
def hash_senha(senha):
    return hashlib.sha256(str(senha).encode('utf-8')).hexdigest()

def init_databases():
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS usuarios 
                        (username TEXT PRIMARY KEY, password TEXT, role TEXT, last_active TEXT)''')
        if not conn.execute("SELECT * FROM usuarios WHERE username='THOMAS'").fetchone():
            conn.execute("INSERT INTO usuarios (username, password, role) VALUES (?, ?, ?)", 
                         ('THOMAS', hash_senha('admin123'), 'ADMIN'))
        conn.commit()

def init_business_db():
    colunas_oficiais = ['ID SISCO', 'STATUS SISCO', 'TIPO LIGACAO SISCO', 'DESCRIÇÃO SERVIÇO SISCO', 'DATA CRIAÇAO SISCO', 'STATUS SAP', 'LEVANTADOR', 'STATUS LIST', 'DATA ENVIO A CAMPO - LIST', 'DATA DE LEVANTAMENTO LIST', 'PROTOCOLO', 'CONTA CONTRATO', 'INSTALACAO', 'NOME DO SOLICITANTE', 'REGIONAL', 'MUNICIPIO', 'ENDEREÇO', 'LOCALIDADE', 'LONGITUDE', 'LATITUDE', 'PONTO DE REFERENCIA', 'TIPO LIGACAO', 'DATA DE VENCIMENTO']
    
    if os.path.exists(DB_PATH): 
        return
        
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            if os.path.exists('NOTAS.xlsx'):
                # Caçador de Abas: Garante a leitura da aba correta (Busca e Governança)
                xls = pd.ExcelFile('NOTAS.xlsx')
                aba_alvo = xls.sheet_names[0] # Fallback para a primeira aba
                
                for nome_aba in xls.sheet_names:
                    aba_limpa = nome_aba.lower().replace('ç', 'c').replace('ã', 'a')
                    if 'busca' in aba_limpa and 'governanca' in aba_limpa:
                        aba_alvo = nome_aba
                        break
                        
                df_legacy = pd.read_excel('NOTAS.xlsx', sheet_name=aba_alvo).fillna("").astype(str)
            else:
                df_legacy = pd.DataFrame(columns=colunas_oficiais)
                
            for col in colunas_oficiais:
                if col not in df_legacy.columns: df_legacy[col] = ""
            df_legacy[colunas_oficiais].to_sql('notas', conn, if_exists='replace', index=False)
                
            if os.path.exists('levantadores.xlsx'): 
                pd.read_excel('levantadores.xlsx').to_sql('equipes', conn, if_exists='replace', index=False)
            elif os.path.exists('base levantador.xlsx'): 
                pd.read_excel('base levantador.xlsx').to_sql('equipes', conn, if_exists='replace', index=False)
            elif os.path.exists('EQUIPES.xlsx'): 
                pd.read_excel('EQUIPES.xlsx').to_sql('equipes', conn, if_exists='replace', index=False)
            else: 
                pd.DataFrame(columns=['Município', 'Estado', 'Levantador', 'Regional', 'Longitude', 'Latitude', 'Equipe']).to_sql('equipes', conn, if_exists='replace', index=False)
    except Exception: pass

def sync_residencias_banco():
    pass

def parse_dates_safe(series):
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    
    s = series.astype(str).str.strip()
    s = s.replace(['', 'nan', 'NAN', 'None', 'NaT', '0', '0.0', '<NA>'], np.nan)
    
    s1 = pd.to_datetime(s, format='%d/%m/%Y', errors='coerce')
    s2 = pd.to_datetime(s, format='%d/%m/%Y %H:%M:%S', errors='coerce')
    s3 = pd.to_datetime(s, format='%Y-%m-%d', errors='coerce')
    s4 = pd.to_datetime(s, format='%Y-%m-%d %H:%M:%S', errors='coerce')
    s5 = pd.to_datetime(s, errors='coerce', dayfirst=True) 
    
    s_num = pd.to_numeric(s, errors='coerce')
    s6 = pd.to_datetime(s_num, origin='1899-12-30', unit='D', errors='coerce')
    
    return s1.combine_first(s2).combine_first(s3).combine_first(s4).combine_first(s6).combine_first(s5)

@st.cache_data(show_spinner=False)
def load_core_data():
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            df_notas = pd.read_sql("SELECT * FROM notas", conn)
            df_equipes = pd.read_sql("SELECT * FROM equipes", conn)
    except Exception:
        df_notas = pd.DataFrame()
        df_equipes = pd.DataFrame()
        
    if 'DATA CRIAÇÃO SISCO' in df_notas.columns and 'DATA CRIAÇAO SISCO' not in df_notas.columns:
        df_notas = df_notas.rename(columns={'DATA CRIAÇÃO SISCO': 'DATA CRIAÇAO SISCO'})

    colunas_data = ['DATA CRIAÇAO SISCO', 'DATA ENVIO A CAMPO - LIST', 'DATA DE LEVANTAMENTO LIST', 'DATA DE VENCIMENTO']
    for col in colunas_data:
        if col in df_notas.columns:
            df_notas[col] = parse_dates_safe(df_notas[col])

    if not df_notas.empty:
        if 'LATITUDE' in df_notas.columns and 'LONGITUDE' in df_notas.columns:
            df_notas['Lat_Mapa'] = pd.to_numeric(df_notas['LATITUDE'].astype(str).str.replace(',', '.'), errors='coerce')
            df_notas['Lon_Mapa'] = pd.to_numeric(df_notas['LONGITUDE'].astype(str).str.replace(',', '.'), errors='coerce')
        else:
            df_notas['Lat_Mapa'] = np.nan
            df_notas['Lon_Mapa'] = np.nan
            
    if not df_equipes.empty:
        col_lat = 'Latitude' if 'Latitude' in df_equipes.columns else 'LATITUDE' if 'LATITUDE' in df_equipes.columns else None
        col_lon = 'Longitude' if 'Longitude' in df_equipes.columns else 'LONGITUDE' if 'LONGITUDE' in df_equipes.columns else None
        
        if col_lat and col_lon:
            df_equipes['Lat_Mapa'] = pd.to_numeric(df_equipes[col_lat].astype(str).str.replace(',', '.'), errors='coerce')
            df_equipes['Lon_Mapa'] = pd.to_numeric(df_equipes[col_lon].astype(str).str.replace(',', '.'), errors='coerce')
        else:
            df_equipes['Lat_Mapa'] = np.nan
            df_equipes['Lon_Mapa'] = np.nan

    resumo_lev = pd.DataFrame(columns=['Levantador', 'Equipe', 'Total_Obras_Real'])
    criticos = []
    todos_levs = []

    if not df_equipes.empty and 'Levantador' in df_equipes.columns:
        if 'Equipe' in df_equipes.columns:
            resumo_lev = df_equipes[['Levantador', 'Equipe']].drop_duplicates()
        else:
            resumo_lev = df_equipes[['Levantador']].drop_duplicates()
            resumo_lev['Equipe'] = 'Sem Equipe'

    if not resumo_lev.empty:
        resumo_lev['Levantador'] = resumo_lev['Levantador'].astype(str).str.strip().str.upper()
        lixos_oficiais = ['SEM LEVANTADOR', 'NAN', 'NONE', '', '0', '0.0']
        resumo_lev = resumo_lev[~resumo_lev['Levantador'].isin(lixos_oficiais)].copy()
            
    if not df_notas.empty and 'LEVANTADOR' in df_notas.columns and 'STATUS LIST' in df_notas.columns:
        mask_em_levantamento = df_notas['STATUS LIST'].astype(str).str.strip().str.upper() == 'EM LEVANTAMENTO'
        df_em_levantamento = df_notas[mask_em_levantamento].copy()
        
        df_em_levantamento['LEV_CLEAN'] = df_em_levantamento['LEVANTADOR'].astype(str).str.strip().str.upper()
        lixos_notas = ['SEM LEVANTADOR', 'NAN', 'NONE', '', '0', '0.0']
        df_em_levantamento = df_em_levantamento[~df_em_levantamento['LEV_CLEAN'].isin(lixos_notas)]
        
        contagem_upper = df_em_levantamento.groupby('LEV_CLEAN').size()
        
        levs_com_obras = set(contagem_upper.index)
        levs_oficiais = set(resumo_lev['Levantador']) if not resumo_lev.empty else set()
        levs_provisorios = list(levs_com_obras - levs_oficiais)
        
        if levs_provisorios:
            df_provisorios = pd.DataFrame({
                'Levantador': levs_provisorios,
                'Equipe': levs_provisorios
            })
            resumo_lev = pd.concat([resumo_lev, df_provisorios], ignore_index=True)
            
        if not resumo_lev.empty:
            resumo_lev['Levantador'] = resumo_lev['Levantador'].astype(str).str.upper()
            resumo_lev['Total_Obras_Real'] = resumo_lev['Levantador'].map(contagem_upper).fillna(0).astype(int)
    else:
        if not resumo_lev.empty:
            resumo_lev['Total_Obras_Real'] = 0
            
    if not resumo_lev.empty:
        criticos = resumo_lev[resumo_lev['Total_Obras_Real'] < 45]['Levantador'].tolist()
        todos_levs = sorted([str(l) for l in resumo_lev['Levantador'].unique() if pd.notna(l) and str(l).strip() != ''])

    return df_notas, df_equipes, resumo_lev, criticos, todos_levs, {}, {}, pd.DataFrame()

def save_notas_to_db(df, acao="Atualização", backup=False):
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        df.to_sql('notas', conn, if_exists='replace', index=False)
    load_core_data.clear()
    return True

# -------------------------------------------------------------------------
# FUNÇÕES DE GESTÃO DO TERRITÓRIO E SIMULADOR (ATUALIZADO)
# -------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def get_base_levantadores():
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            return pd.read_sql("SELECT * FROM equipes", conn)
    except Exception:
        return pd.DataFrame()

def save_base_levantadores(df):
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        df.to_sql('equipes', conn, if_exists='replace', index=False)
    get_base_levantadores.clear()
    load_core_data.clear()
    return True

# Busca as equipes configuradas no Banco de Dados (Persistência do Simulador)
def get_equipes_base_simulador():
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS config_simulador (regional TEXT PRIMARY KEY, equipes_atuais INTEGER)')
            df = pd.read_sql("SELECT * FROM config_simulador", conn)
            return dict(zip(df['regional'], df['equipes_atuais']))
    except Exception:
        return {}

# Salva as equipes simuladas no Banco APÓS checar a senha do usuário
def save_equipes_base_simulador(novas_equipes_dict, username, senha_digitada):
    try:
        username = username.strip().upper()
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT password FROM usuarios WHERE username=?", (username,))
            row = cursor.fetchone()
            
            # Se o usuário não existir ou a senha não bater, aborta e retorna Falso
            if not row or row[0] != hash_senha(senha_digitada):
                return False 
            
            # Se passou pela senha, salva!
            conn.execute('CREATE TABLE IF NOT EXISTS config_simulador (regional TEXT PRIMARY KEY, equipes_atuais INTEGER)')
            for reg, qtd in novas_equipes_dict.items():
                conn.execute("INSERT OR REPLACE INTO config_simulador (regional, equipes_atuais) VALUES (?, ?)", (reg, int(qtd)))
            conn.commit()
        return True
    except Exception:
        return False

# -------------------------------------------------------------------------

def vectorized_haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat/2.0)**2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon/2.0)**2
    c = 2 * np.arcsin(np.sqrt(a))
    return 6371 * c

def parse_kmz_advanced(caminho):
    return pd.DataFrame(), pd.DataFrame(), None

def calcular_sla_vetorizado(df):
    if df.empty: return df
    df_copy = df.copy()
    if 'Status_SLA' not in df_copy.columns:
        df_copy['Status_SLA'] = 'No Prazo'
    return df_copy
