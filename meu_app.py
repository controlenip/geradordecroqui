import streamlit as st

# ==========================================
# 1. CONFIGURAÇÃO GLOBAL DA PÁGINA (Deve ser a 1ª linha do app)
# ==========================================
st.set_page_config(
    page_title="CONTROLE DEMANDA - MA", 
    page_icon="🗺️", 
    layout="wide", 
    initial_sidebar_state="expanded"
)

# ==========================================
# 2. INICIALIZAÇÃO E IMPORTAÇÃO DO BANCO
# ==========================================
# IMPORTANTE: Adicionamos o verify_password aqui!
from utils.db_config import init_db, get_connection, hash_password, verify_password

# Garante que as tabelas existem antes de qualquer coisa acontecer
init_db()

# ==========================================
# 3. IMPORTAÇÕES DAS TELAS (VIEWS)
# ==========================================
try: from views.painel import view_painel_executivo
except ImportError: view_painel_executivo = None

try: from views.mapa import view_mapa
except ImportError: view_mapa = None

try: from views.roteirizador import view_roteirizador
except ImportError: view_roteirizador = None

try: from views.modulo_croqui import view_gerador_croqui
except ImportError: view_gerador_croqui = None

try: from views.carga import view_carga 
except ImportError: view_carga = None

try: from views.levantadores import view_levantadores
except ImportError: view_levantadores = None

try: from views.acessos import view_acessos
except ImportError: view_acessos = None

try: from views.governanca import view_governanca
except ImportError: view_governanca = None

try: from views.simulador import view_simulador
except ImportError: view_simulador = None

# ==========================================
# 4. CONTROLE DE SESSÃO E LOGIN
# ==========================================
if "autenticado" not in st.session_state:
    st.session_state.autenticado = False
if "usuario" not in st.session_state:
    st.session_state.usuario = None
if "perfil_usuario" not in st.session_state:
    st.session_state.perfil_usuario = None

def fazer_login(username, password):
    """Verifica as credenciais corretamente usando o bcrypt e inclui Override Mestre"""
    user_upper = username.strip().upper()
    
    # 🔴 OVERRIDE MESTRE: Acesso garantido
    if user_upper == "THOMAS" and password == "admin123":
        st.session_state.autenticado = True
        st.session_state.usuario = "THOMAS"
        st.session_state.perfil_usuario = "ADMIN"
        return True

    conn = get_connection()
    cursor = conn.cursor()
    
    # Forma correta de lidar com bcrypt: Busca a hash do banco pelo nome de usuário
    cursor.execute("SELECT password, role FROM usuarios WHERE username = ?", (user_upper,))
    resultado = cursor.fetchone()
    conn.close()
    
    if resultado:
        senha_salva_no_banco = resultado[0]
        perfil = resultado[1]
        
        # Compara a senha digitada com a hash salva usando a biblioteca
        if verify_password(password, senha_salva_no_banco):
            st.session_state.autenticado = True
            st.session_state.usuario = user_upper
            st.session_state.perfil_usuario = perfil
            return True
            
    return False

def tela_login():
    """Interface da Tela de Autenticação"""
    st.markdown("<br><br>", unsafe_allow_html=True)
    st.markdown("<h1 style='text-align: center;'>🔐 CONTROLE DEMANDA - MA</h1>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center;'>Faça login para continuar</p>", unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("form_login"):
            usuario_input = st.text_input("Usuário (Login)")
            senha_input = st.text_input("Senha", type="password")
            
            submit_login = st.form_submit_button("Entrar", use_container_width=True)
            
            if submit_login:
                if fazer_login(usuario_input, senha_input):
                    st.success("✅ Acesso concedido! Carregando...")
                    st.rerun()
                else:
                    st.error("❌ Usuário ou senha incorretos.")

# ==========================================
# 5. APLICATIVO PRINCIPAL (MAESTRO)
# ==========================================
def main():
    # Bloqueia a execução se não estiver logado
    if not st.session_state.autenticado:
        tela_login()
        return

    # A PARTIR DAQUI, O USUÁRIO ESTÁ LOGADO
    
    # ==========================================
    # BARRA LATERAL (MENU)
    # ==========================================
    st.sidebar.markdown("### 👤 CONTROLE DEMANDA - MA")
    st.sidebar.markdown(f"**Usuário:** {st.session_state.usuario}")
    st.sidebar.markdown(f"**Perfil:** {st.session_state.perfil_usuario}")
    
    if st.sidebar.button("🚪 SAIR / DESLOGAR", use_container_width=True):
        st.session_state.clear()
        st.rerun()
        
    st.sidebar.markdown("---")

    menu_opcoes = [
        "📊 Painel Executivo",
        "🗺️ Mapa de Obras",
        "🚙 Roteirizador Operacional",
        "🗺️ Gerador de Croquis Automático", 
        "☁️ Carga De Lotes",
        "📇 Levantadores",
        "🛡️ Gerenciamento De Acessos",
        "🔍 Busca E Governança",
        "⚙️ Simulador De Alocação"
    ]
    
    pagina_selecionada = st.sidebar.radio(
        "Navegação do Sistema", 
        menu_opcoes, 
        label_visibility="collapsed"
    )

    st.sidebar.markdown("---") 

    # ==========================================
    # ROTEADOR DE TELAS
    # ==========================================
    if pagina_selecionada == "📊 Painel Executivo":
        if view_painel_executivo: view_painel_executivo()
        else: st.error("⚠️ Tela não encontrada.")
        
    elif pagina_selecionada == "🗺️ Mapa de Obras":
        if view_mapa: view_mapa()
        else: st.error("⚠️ Tela não encontrada.")
        
    elif pagina_selecionada == "🚙 Roteirizador Operacional":
        if view_roteirizador: view_roteirizador()
        else: st.error("⚠️ Tela não encontrada.")

    elif pagina_selecionada == "🗺️ Gerador de Croquis Automático":
        if view_gerador_croqui: view_gerador_croqui()
        else: st.error("⚠️ Tela não encontrada.")

    elif pagina_selecionada == "☁️ Carga De Lotes":
        if view_carga: view_carga()
        else: st.error("⚠️ Tela não encontrada.")

    elif pagina_selecionada == "📇 Levantadores":
        if view_levantadores: view_levantadores()
        else: st.error("⚠️ Tela não encontrada.")

    elif pagina_selecionada == "🛡️ Gerenciamento De Acessos":
        if view_acessos: view_acessos()
        else: st.error("⚠️ Tela não encontrada.")

    elif pagina_selecionada == "🔍 Busca E Governança":
        if view_governanca: view_governanca()
        else: st.error("⚠️ Tela não encontrada.")

    elif pagina_selecionada == "⚙️ Simulador De Alocação":
        if view_simulador: view_simulador()
        else: st.error("⚠️ Tela não encontrada.")

if __name__ == "__main__":
    main()
