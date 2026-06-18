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

# ── helpers ───────────────────────────────────────────────────────────────────

AUSENCIA_KEYWORDS = [
    "férias", "ferias", "licença", "licenca", "afastamento",
    "atestado", "folga", "feriado", "ausência", "ausencia",
]

def is_ausencia(nome_projeto: str) -> bool:
    return any(k in str(nome_projeto).lower() for k in AUSENCIA_KEYWORDS)


def load_csv(uploaded_file) -> pd.DataFrame:
    raw = uploaded_file.read()
    encoding = "utf-8"
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

st.success(f"✅ {len(df_raw):,} registros carregados.")

# ── sidebar — filtros ─────────────────────────────────────────────────────────

with st.sidebar:
    st.header("🔎 Filtros")

    data_min = df_raw["data"].min().date() if not df_raw["data"].isna().all() else date.today()
    data_max = df_raw["data"].max().date() if not df_raw["data"].isna().all() else date.today()

    col1, col2 = st.columns(2)
    with col1:
        dt_ini = st.date_input("De", value=data_min, min_value=data_min, max_value=data_max)
    with col2:
        dt_fim = st.date_input("Até", value=data_max, min_value=data_min, max_value=data_max)

    st.divider()

    excluir_prodam = st.checkbox("Excluir internos PRODAM", value=True)
    excluir_ausencias = st.checkbox("Excluir ausências (férias, licenças…)", value=True)

    st.divider()

    # Base pré-filtrada para popular os selects
    df_base = df_raw.copy()
    if excluir_prodam:
        df_base = df_base[df_base["cliente"].str.upper().ne("PRODAM")]
    if excluir_ausencias:
        df_base = df_base[~df_base["nome_projeto"].apply(is_ausencia)]

    # ── campo de texto livre para cliente ──
    st.markdown("**Cliente**")
    busca_cliente = st.text_input(
        "Digite parte do nome do cliente",
        placeholder="ex: SMSUB, SMS, SEME…",
        label_visibility="collapsed",
    )

    # Filtra clientes pelo que foi digitado
    clientes_todos = sorted(df_base["cliente"].dropna().unique().tolist())
    if busca_cliente.strip():
        clientes_match = [c for c in clientes_todos if busca_cliente.strip().lower() in c.lower()]
    else:
        clientes_match = clientes_todos

    # Mostra lista dos clientes encontrados para confirmação
    if busca_cliente.strip() and clientes_match:
        st.caption(f"Encontrado(s): {', '.join(clientes_match)}")
    elif busca_cliente.strip() and not clientes_match:
        st.warning("Nenhum cliente encontrado.")

    st.divider()

    # ── contratos filtrados pelo cliente ──
    df_proj_base = df_base[df_base["cliente"].isin(clientes_match)] if clientes_match else df_base
    projetos_disponiveis = sorted(df_proj_base["nome_projeto"].dropna().unique().tolist())

    projetos_sel = st.multiselect(
        "Contrato / Projeto",
        options=projetos_disponiveis,
        default=projetos_disponiveis,
        help="Lista atualizada conforme o cliente digitado acima.",
    )

    st.divider()
    gerar = st.button("⚡ Gerar prévia", type="primary", use_container_width=True)

# ── aplicar filtros ───────────────────────────────────────────────────────────

df = df_raw.copy()
df = df[(df["data"].dt.date >= dt_ini) & (df["data"].dt.date <= dt_fim)]

if excluir_prodam:
    df = df[df["cliente"].str.upper().ne("PRODAM")]
if excluir_ausencias:
    df = df[~df["nome_projeto"].apply(is_ausencia)]

# Filtra clientes (só se digitou algo)
if busca_cliente.strip() and clientes_match:
    df = df[df["cliente"].isin(clientes_match)]

if projetos_sel:
    df = df[df["nome_projeto"].isin(projetos_sel)]

# Coluna GDS: aceita 'gds' ou 'gds_csv'
gds_col = "gds" if "gds" in df.columns else ("gds_csv" if "gds_csv" in df.columns else None)
sort_cols = ["nome_projeto"] + ([gds_col] if gds_col else []) + ["atividade", "nome", "data"]
df = df.sort_values(sort_cols)

# ── preview antes de gerar ────────────────────────────────────────────────────

if not gerar:
    st.markdown("---")
    c1, c2, c3 = st.columns(3)
    c1.metric("Registros filtrados", f"{len(df):,}")
    c2.metric("Contratos", df["nome_projeto"].nunique())
    c3.metric("Total de horas", fmt_horas(df["horas"].sum()))
    st.caption("Clique em **⚡ Gerar prévia** para montar o relatório HTML.")
    st.stop()

# ── montar estrutura hierárquica ──────────────────────────────────────────────

if df.empty:
    st.warning("Nenhum registro encontrado com os filtros aplicados.")
    st.stop()

for col in ["ordem_servico", "tipo_demanda", "gdp"]:
    if col not in df.columns:
        df[col] = ""

tree = {}
for _, row in df.iterrows():
    proj  = str(row.get("nome_projeto", "")).strip() or "(sem projeto)"
    gds   = str(row.get(gds_col, "")).strip() if gds_col else "(sem GDS)"
    gds   = gds or "(sem GDS)"
    ativ  = str(row.get("atividade", "")).strip() or "—"
    titulo = str(row.get("titulo_atividade", "")).strip() or "—"
    ativ_key = f"{ativ} — {titulo}"

    tree.setdefault(proj, {})
    tree[proj].setdefault(gds, {})
    tree[proj][gds].setdefault(ativ_key, {"atividade": ativ, "titulo": titulo, "linhas": []})
    tree[proj][gds][ativ_key]["linhas"].append({
        "nome":      str(row.get("nome", "")).strip(),
        "rf":        str(row.get("rf", "")).strip(),
        "data":      row["data"].strftime("%d/%m/%Y") if pd.notna(row["data"]) else "—",
        "horas":     row["horas"],
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
tree_json     = json.dumps(tree, ensure_ascii=False, default=str)

csv_cols = ["nome_projeto", gds_col or "gds", "atividade", "titulo_atividade",
            "nome", "rf", "data", "horas", "ordem_servico", "tipo_demanda"]
csv_cols_present = [c for c in csv_cols if c in df.columns]
df_export = df[csv_cols_present].copy()
df_export["data"]  = df["data"].dt.strftime("%d/%m/%Y")
df_export["horas"] = df["horas"].apply(lambda x: f"{x:.2f}".replace(".", ","))
csv_str = df_export.to_csv(index=False, sep=";", encoding="utf-8")

html_out = (
    html_template
    .replace("%%PERIODO%%",        periodo_str)
    .replace("%%GERADO_EM%%",      gerado_em)
    .replace("%%TOTAL_HORAS%%",    fmt_horas(total_geral))
    .replace("%%TOTAL_REGISTROS%%", str(len(df)))
    .replace("%%TREE_JSON%%",      tree_json)
    .replace("%%CSV_DATA%%",       json.dumps(csv_str, ensure_ascii=False))
)

# ── resultado ─────────────────────────────────────────────────────────────────

st.markdown("---")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Registros",      f"{len(df):,}")
c2.metric("Contratos",      df["nome_projeto"].nunique())
c3.metric("Colaboradores",  df["rf"].nunique())
c4.metric("Total de horas", fmt_horas(total_geral))

st.download_button(
    label="⬇️ Baixar relatório HTML",
    data=html_out.encode("utf-8"),
    file_name=f"previa_faturamento_{dt_ini.strftime('%Y%m%d')}_{dt_fim.strftime('%Y%m%d')}.html",
    mime="text/html",
    type="primary",
)

st.markdown("### Prévia por contrato")
for proj, gdss in tree.items():
    total_proj = sum(
        ln["horas"]
        for gds_data in gdss.values()
        for ativ_data in gds_data.values()
        for ln in ativ_data["linhas"]
    )
    with st.expander(f"📁 {proj}  —  {fmt_horas(total_proj)}", expanded=False):
        for gds, atividades in gdss.items():
            total_gds = sum(
                ln["horas"]
                for ad in atividades.values()
                for ln in ad["linhas"]
            )
            st.markdown(f"**GDS: {gds}** — {fmt_horas(total_gds)}")
            rows = []
            for ativ_key, ad in atividades.items():
                for ln in ad["linhas"]:
                    rows.append({
                        "Atividade": ativ_key,
                        "Nome": ln["nome"],
                        "RF":   ln["rf"],
                        "Data": ln["data"],
                        "Horas": ln["horas_fmt"],
                    })
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
