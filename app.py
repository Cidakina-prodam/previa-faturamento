import streamlit as st
import pandas as pd
import json
import io
from datetime import datetime, date
from pathlib import Path

st.set_page_config(
    page_title="Prévia de Faturamento — PRODAM",
    page_icon="📊",
    layout="wide",
)

st.title("📊 Prévia de Faturamento")
st.caption("Visualização de lançamentos de horas por contrato — PRODAM")

# ── constantes ────────────────────────────────────────────────────────────────

CLIENTES_GDS1 = ["SMS", "HSPM", "SEME", "SMDET", "SMADS", "SMDHC", "SPCINE", "SMPED", "FTM", "SMC"]

AUSENCIA_KEYWORDS = [
    "férias", "ferias", "licença", "licenca", "afastamento",
    "atestado", "folga", "feriado", "ausência", "ausencia",
]

# ── helpers ───────────────────────────────────────────────────────────────────

def is_ausencia(nome_projeto: str) -> bool:
    return any(k in str(nome_projeto).lower() for k in AUSENCIA_KEYWORDS)


def load_csv(uploaded_file) -> pd.DataFrame:
    raw = uploaded_file.read()
    encoding = "utf-8"
    sample = ""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            sample = raw[:4096].decode(enc)
            encoding = enc
            break
        except UnicodeDecodeError:
            continue
    sep = "\t" if sample.count("\t") > sample.count(";") else ";"
    df = pd.read_csv(io.BytesIO(raw), sep=sep, encoding=encoding, dtype=str)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    return df


def parse_dates(df):
    df["data"] = pd.to_datetime(df["data"], dayfirst=True, errors="coerce")
    return df


def parse_horas(df):
    df["horas"] = (
        df["horas"].astype(str)
        .str.replace(",", ".", regex=False)
        .pipe(pd.to_numeric, errors="coerce")
        .fillna(0)
    )
    return df


def fmt_horas(h: float) -> str:
    total_min = round(h * 60)
    hh = total_min // 60
    mm = total_min % 60
    return f"{hh}h{mm:02d}"


def clean_val(v) -> str:
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none", "n/d") else s


# ── upload ────────────────────────────────────────────────────────────────────

uploaded = st.file_uploader(
    "📂 CSV de lançamentos",
    type=["csv"],
    help="Separador TAB ou `;`, encoding UTF-8 ou Latin-1.",
)

if not uploaded:
    st.info("Faça o upload do CSV de lançamentos para começar.")
    st.stop()

with st.spinner("Carregando CSV…"):
    df_raw = load_csv(uploaded)
    df_raw = parse_dates(df_raw)
    df_raw = parse_horas(df_raw)

required_cols = {"nome", "rf", "cliente", "nome_projeto", "atividade", "titulo_atividade", "data", "horas"}
missing = required_cols - set(df_raw.columns)
if missing:
    st.error(f"Colunas não encontradas no CSV: `{'`, `'.join(sorted(missing))}`")
    st.stop()

# Reseta datas ao trocar de arquivo
arquivo_atual = uploaded.name
if st.session_state.get("arquivo_carregado") != arquivo_atual:
    st.session_state["arquivo_carregado"] = arquivo_atual
    st.session_state.pop("dt_ini", None)
    st.session_state.pop("dt_fim", None)

st.success(f"✅ {len(df_raw):,} registros carregados.")

# ── sidebar — filtros ─────────────────────────────────────────────────────────

with st.sidebar:
    st.header("🔎 Filtros")

    data_min = df_raw["data"].min().date() if not df_raw["data"].isna().all() else date.today()
    data_max = df_raw["data"].max().date() if not df_raw["data"].isna().all() else date.today()

    col1, col2 = st.columns(2)
    with col1:
        dt_ini = st.date_input("De", value=st.session_state.get("dt_ini", data_min),
                               min_value=data_min, max_value=data_max, format="DD/MM/YYYY", key="dt_ini")
    with col2:
        dt_fim = st.date_input("Até", value=st.session_state.get("dt_fim", data_max),
                               min_value=data_min, max_value=data_max, format="DD/MM/YYYY", key="dt_fim")

    st.divider()

    excluir_prodam = st.checkbox("Excluir internos PRODAM", value=True)
    excluir_ausencias = st.checkbox("Excluir ausências (férias, licenças…)", value=True)

    st.divider()

    # ── clientes GDS1 pré-selecionados (checkboxes) ──
    st.markdown("**Clientes GDS1**")
    st.caption("Pré-selecionados. Desmarque para excluir.")

    clientes_existentes_no_csv = set(df_raw["cliente"].dropna().unique().tolist())

    clientes_selecionados = []
    cols_chk = st.columns(2)
    for i, cli in enumerate(CLIENTES_GDS1):
        col = cols_chk[i % 2]
        presente = cli in clientes_existentes_no_csv
        label = cli if presente else f"{cli} ⚪"
        marcado = col.checkbox(label, value=True, key=f"chk_{cli}",
                               help=None if presente else "Não encontrado no CSV carregado")
        if marcado:
            clientes_selecionados.append(cli)

    with st.expander("➕ Outros clientes (fora da lista GDS1)"):
        outros_clientes = sorted(clientes_existentes_no_csv - set(CLIENTES_GDS1) - {"PRODAM"})
        outros_sel = st.multiselect("Adicionar outros clientes", options=outros_clientes, default=[])
        clientes_selecionados += outros_sel

    st.divider()
    gerar = st.button("⚡ Gerar prévia", type="primary", use_container_width=True)
    st.caption("Filtros de **Cliente**, **Contrato** e a **ordenação** (GDS/GDP, Colaborador, Data, Atividade) ficam disponíveis **dentro do relatório HTML**.")

# ── aplicar filtros ───────────────────────────────────────────────────────────

df = df_raw.copy()
df = df[(df["data"].dt.date >= dt_ini) & (df["data"].dt.date <= dt_fim)]

if excluir_prodam:
    df = df[df["cliente"].str.upper().ne("PRODAM")]
if excluir_ausencias:
    df = df[~df["nome_projeto"].apply(is_ausencia)]

if clientes_selecionados:
    df = df[df["cliente"].isin(clientes_selecionados)]
else:
    st.warning("Nenhum cliente selecionado — o relatório ficará vazio.")

# Coluna GDS/GDP: aceita 'gds'/'gds_csv' e 'gdp'/'gdp_csv'
gds_col = "gds" if "gds" in df.columns else ("gds_csv" if "gds_csv" in df.columns else None)
gdp_col = "gdp" if "gdp" in df.columns else ("gdp_csv" if "gdp_csv" in df.columns else None)

df = df.sort_values(["cliente", "nome_projeto", "data", "nome"])

# ── preview antes de gerar ────────────────────────────────────────────────────

if not gerar:
    st.markdown("---")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Registros filtrados", f"{len(df):,}")
    c2.metric("Clientes", df["cliente"].nunique())
    c3.metric("Contratos", df["nome_projeto"].nunique())
    c4.metric("Total de horas", fmt_horas(df["horas"].sum()))
    st.caption("Clique em **⚡ Gerar prévia** para montar o relatório HTML.")
    st.stop()

# ── montar lista plana de registros ───────────────────────────────────────────

if df.empty:
    st.warning("Nenhum registro encontrado com os filtros aplicados.")
    st.stop()

for col in ["ordem_servico", "tipo_demanda"]:
    if col not in df.columns:
        df[col] = ""


def get_gds_gdp_label(row) -> str:
    gds_val = clean_val(row.get(gds_col, "")) if gds_col else ""
    gdp_val = clean_val(row.get(gdp_col, "")) if gdp_col else ""
    if gds_val and gdp_val:
        return f"{gds_val} / GDP {gdp_val}"
    if gds_val:
        return gds_val
    if gdp_val:
        return f"GDP {gdp_val}"
    return "(sem GDS/GDP)"


records = []
for _, row in df.iterrows():
    cliente = str(row.get("cliente", "")).strip() or "(sem cliente)"
    proj    = str(row.get("nome_projeto", "")).strip() or "(sem projeto)"
    ativ    = str(row.get("atividade", "")).strip() or "—"
    titulo  = str(row.get("titulo_atividade", "")).strip() or "—"
    nome    = str(row.get("nome", "")).strip()
    data_fmt = row["data"].strftime("%d/%m/%Y") if pd.notna(row["data"]) else "—"
    data_iso = row["data"].strftime("%Y-%m-%d") if pd.notna(row["data"]) else ""

    records.append({
        "cliente":   cliente,
        "proj":      proj,
        "contrato":  str(row.get("contrato", "")).strip(),
        "gds_gdp":   get_gds_gdp_label(row),
        "atividade": ativ,
        "titulo":    titulo,
        "nome":      nome,
        "rf":        str(row.get("rf", "")).strip(),
        "data":      data_fmt,
        "data_iso":  data_iso,
        "horas":     float(row["horas"]) if pd.notna(row["horas"]) else 0.0,
        "horas_fmt": fmt_horas(row["horas"]),
        "os":        str(row.get("ordem_servico", "")).strip(),
        "tipo":      str(row.get("tipo_demanda", "")).strip(),
    })

total_geral  = df["horas"].sum()
periodo_str  = f"{dt_ini.strftime('%d/%m/%Y')} a {dt_fim.strftime('%d/%m/%Y')}"
gerado_em    = datetime.now().strftime("%d/%m/%Y %H:%M")

# ── carregar template ─────────────────────────────────────────────────────────

template_path = Path(__file__).parent / "template.html"
if not template_path.exists():
    st.error("Arquivo `template.html` não encontrado na mesma pasta que `app.py`.")
    st.stop()

html_template = template_path.read_text(encoding="utf-8")

try:
    records_json = json.dumps(records, ensure_ascii=False, allow_nan=False)
except ValueError as e:
    st.error(f"Erro ao montar o relatório: dados numéricos inválidos encontrados ({e}). Verifique a coluna `horas` do CSV.")
    st.stop()

# CSV bruto para exportação embutida no HTML
csv_cols = ["cliente", "nome_projeto", "contrato"]
if gds_col: csv_cols.append(gds_col)
if gdp_col: csv_cols.append(gdp_col)
csv_cols += ["atividade", "titulo_atividade", "nome", "rf", "data", "horas",
             "ordem_servico", "tipo_demanda"]
csv_cols_present = [c for c in csv_cols if c in df.columns]
df_export = df[csv_cols_present].copy()
df_export["data"]  = df["data"].dt.strftime("%d/%m/%Y")
df_export["horas"] = df["horas"].apply(lambda x: f"{x:.2f}".replace(".", ","))
csv_str = df_export.to_csv(index=False, sep=";", encoding="utf-8")

html_out = (
    html_template
    .replace("%%PERIODO%%",         periodo_str)
    .replace("%%GERADO_EM%%",       gerado_em)
    .replace("%%TOTAL_HORAS%%",     fmt_horas(total_geral))
    .replace("%%TOTAL_REGISTROS%%", str(len(df)))
    .replace("%%RECORDS_JSON%%",    records_json)
    .replace("%%CSV_DATA%%",        json.dumps(csv_str, ensure_ascii=False))
)

# ── resultado ─────────────────────────────────────────────────────────────────

st.markdown("---")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Registros",      f"{len(df):,}")
c2.metric("Clientes",       df["cliente"].nunique())
c3.metric("Contratos",      df["nome_projeto"].nunique())
c4.metric("Total de horas", fmt_horas(total_geral))

st.download_button(
    label="⬇️ Baixar relatório HTML",
    data=html_out.encode("utf-8"),
    file_name=f"previa_faturamento_{dt_ini.strftime('%Y%m%d')}_{dt_fim.strftime('%Y%m%d')}.html",
    mime="text/html",
    type="primary",
)

st.markdown("### Prévia por cliente / contrato")
for cliente in df["cliente"].unique():
    df_cli = df[df["cliente"] == cliente]
    total_cli = df_cli["horas"].sum()
    with st.expander(f"🏢 {cliente}  —  {fmt_horas(total_cli)}", expanded=False):
        for proj in df_cli["nome_projeto"].unique():
            df_proj = df_cli[df_cli["nome_projeto"] == proj]
            total_proj = df_proj["horas"].sum()
            st.markdown(f"**📁 {proj}** — {fmt_horas(total_proj)}")
            cols_show = ["nome", "rf", "data", "horas"]
            if gds_col: cols_show.insert(2, gds_col)
            if gdp_col: cols_show.insert(3, gdp_col)
            st.dataframe(
                df_proj[cols_show].assign(
                    data=df_proj["data"].dt.strftime("%d/%m/%Y"),
                    horas=df_proj["horas"].apply(fmt_horas),
                ),
                use_container_width=True,
                hide_index=True,
            )
