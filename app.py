import streamlit as st
import pandas as pd
import json
import io
import re
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


def fmt_valor(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def contrato_key_de(row) -> str:
    """Chave de contrato usada para casar com a tabela de valores: usa `contrato`
    quando existir, senão cai para `nome_projeto` (mesmo critério do agrupamento
    do relatório HTML)."""
    contrato_val = str(row.get("contrato", "")).strip()
    return contrato_val if contrato_val else str(row.get("nome_projeto", "")).strip()


# ── valores (R$) — matching com o backup do portal GDS-1 ──────────────────────

def _digits(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


def build_valor_hora_lookup(contratos_json: list) -> dict:
    """A partir da lista `contratos` do backup do GDS-1, monta dois índices:
    - "full": chave_digitos(contrato+proposta) -> entrada  (mais específico)
    - "contrato": chave_digitos(contrato) -> lista de entradas (fallback, usado
      quando a `proposta` no backup não segue o padrão "TA xx/yyyy" e por isso
      não bate com o campo `contrato` do CSV — ex.: proposta="PA-SMDET v3.0").
    Quando duas entradas colidem na mesma chave "full", prioriza a 'Ativo'."""
    lookup_full = {}
    lookup_contrato = {}
    for c in contratos_json:
        vlr = c.get("vlrHora")
        if vlr is None:
            continue
        contrato = str(c.get("contrato", ""))
        proposta = str(c.get("proposta", ""))
        key_contrato = _digits(contrato)
        if not key_contrato:
            continue
        key_full = key_contrato + _digits(proposta)
        entry = {
            "valor_hora": float(vlr),
            "status": str(c.get("status", "")),
            "label": f"{contrato} - {proposta}".strip(" -"),
        }
        existing = lookup_full.get(key_full)
        if existing is None or (entry["status"] == "Ativo" and existing["status"] != "Ativo"):
            lookup_full[key_full] = entry
        lookup_contrato.setdefault(key_contrato, []).append(entry)
    return {"full": lookup_full, "contrato": lookup_contrato}


def _melhor_entre_candidatos(candidatos):
    if len(candidatos) == 1:
        return candidatos[0]["valor_hora"], "auto"
    ativos = [c for c in candidatos if c["status"] == "Ativo"]
    if len(ativos) == 1:
        return ativos[0]["valor_hora"], "auto"
    return None, "ambiguo"


def match_valor_hora(contrato_csv: str, lookup: dict):
    """Tenta casar o `contrato` do CSV de lançamentos com uma entrada do backup
    GDS-1, tolerando diferenças de formatação (barra/traço/espaço, TA sem ano,
    proposta com nomenclatura livre etc). Retorna (valor_hora ou None,
    status: 'auto' | 'ambiguo' | 'nao_encontrado' | 'sem_contrato')."""
    key_csv = _digits(contrato_csv)
    if not key_csv:
        return None, "sem_contrato"

    lookup_full = lookup["full"]
    lookup_contrato = lookup["contrato"]

    # 1) match exato contrato+proposta
    if key_csv in lookup_full:
        return lookup_full[key_csv]["valor_hora"], "auto"

    # 2) uma das chaves é prefixo da outra (ex.: proposta sem ano no backup)
    candidatos = [v for k, v in lookup_full.items() if key_csv.startswith(k) or k.startswith(key_csv)]
    if candidatos:
        return _melhor_entre_candidatos(candidatos)

    # 3) fallback: ignora a proposta, casa só pelo número do contrato
    candidatos2 = [
        entry
        for key_contrato, entries in lookup_contrato.items()
        if key_csv.startswith(key_contrato)
        for entry in entries
    ]
    if candidatos2:
        return _melhor_entre_candidatos(candidatos2)

    return None, "nao_encontrado"


# ── upload ────────────────────────────────────────────────────────────────────

uploaded_files = st.file_uploader(
    "📂 CSV de lançamentos",
    type=["csv"],
    accept_multiple_files=True,
    help="Separador TAB ou `;`, encoding UTF-8 ou Latin-1. Pode selecionar vários arquivos (ex.: um por mês) — eles são consolidados automaticamente.",
)

if not uploaded_files:
    st.info("Faça o upload de um ou mais CSVs de lançamentos para começar.")
    st.stop()

required_cols = {"nome", "rf", "cliente", "nome_projeto", "atividade", "titulo_atividade", "data", "horas"}

with st.spinner(f"Carregando {len(uploaded_files)} arquivo(s)…"):
    dfs = []
    colunas_ref = None
    avisos_colunas = []
    contagem_por_arquivo = []

    for f in uploaded_files:
        df_i = load_csv(f)

        # remove linhas completamente em branco (mesma regra da csv-merge)
        df_i.dropna(how="all", inplace=True)
        df_i = df_i[~df_i.apply(lambda r: r.astype(str).str.strip().eq("").all(), axis=1)]

        cols_i = set(df_i.columns)
        if colunas_ref is None:
            colunas_ref = cols_i
        elif cols_i != colunas_ref:
            faltando = colunas_ref - cols_i
            extras = cols_i - colunas_ref
            detalhe = []
            if faltando:
                detalhe.append(f"faltam: {', '.join(sorted(faltando))}")
            if extras:
                detalhe.append(f"a mais: {', '.join(sorted(extras))}")
            avisos_colunas.append(f"**{f.name}** — {'; '.join(detalhe)}")

        contagem_por_arquivo.append((f.name, len(df_i)))
        dfs.append(df_i)

    df_raw = pd.concat(dfs, ignore_index=True)
    df_raw = parse_dates(df_raw)
    df_raw = parse_horas(df_raw)

missing = required_cols - set(df_raw.columns)
if missing:
    st.error(f"Colunas não encontradas no CSV: `{'`, `'.join(sorted(missing))}`")
    st.stop()

if avisos_colunas:
    st.warning(
        "⚠️ Colunas divergentes entre arquivos (os registros foram mantidos, mas confira se é esperado):\n\n"
        + "\n".join(f"- {a}" for a in avisos_colunas)
    )

# Reseta datas ao trocar o conjunto de arquivos
arquivo_atual = tuple(sorted((f.name, f.size) for f in uploaded_files))
if st.session_state.get("arquivo_carregado") != arquivo_atual:
    st.session_state["arquivo_carregado"] = arquivo_atual
    st.session_state.pop("dt_ini", None)
    st.session_state.pop("dt_fim", None)

resumo_arquivos = " · ".join(f"{nome} ({qtd:,})" for nome, qtd in contagem_por_arquivo)
st.success(f"✅ {len(df_raw):,} registros carregados de {len(uploaded_files)} arquivo(s): {resumo_arquivos}")

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

    st.markdown("**💰 Valores (R$)**")
    incluir_valores = st.checkbox(
        "Incluir valores no relatório", value=False,
        help="Adiciona R$/hora por contrato aos lançamentos e ao relatório HTML. "
             "Deixe desmarcado para relatórios que vão circular sem informação de valores.",
    )

    backup_gds1_file = None
    config_valores_file = None
    if incluir_valores:
        backup_gds1_file = st.file_uploader(
            "Backup do portal GDS-1 (.json)", type=["json"], key="backup_gds1",
            help="Exportado do portal GDS-1 — o app tenta casar automaticamente o valor/hora de cada contrato.",
        )
        config_valores_file = st.file_uploader(
            "Configuração de valores salva (.json)", type=["json"], key="config_valores",
            help="Arquivo baixado em um processamento anterior deste app — pré-preenche os valores sem digitar de novo.",
        )

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

# ── valores por contrato ───────────────────────────────────────────────────────

valor_hora_por_contrato = {}

if incluir_valores and not df.empty:
    lookup_gds1 = {}
    if backup_gds1_file is not None:
        try:
            backup_data = json.loads(backup_gds1_file.getvalue().decode("utf-8"))
            lookup_gds1 = build_valor_hora_lookup(backup_data.get("contratos", []))
        except Exception as e:
            st.error(f"Erro ao ler o backup do GDS-1: {e}")

    config_salva = {}
    if config_valores_file is not None:
        try:
            config_salva = json.loads(config_valores_file.getvalue().decode("utf-8"))
        except Exception as e:
            st.error(f"Erro ao ler a configuração de valores: {e}")

    df_tmp = df.copy()
    df_tmp["_contrato_key"] = df_tmp.apply(contrato_key_de, axis=1)
    contratos_presentes = (
        df_tmp[["cliente", "_contrato_key"]]
        .drop_duplicates()
        .sort_values(["cliente", "_contrato_key"])
    )

    linhas_valor = []
    for _, r in contratos_presentes.iterrows():
        chave = r["_contrato_key"]
        origem = "✏️ Manual"
        valor = 0.0
        if chave in config_salva:
            valor = float(config_salva[chave])
            origem = "💾 Config. salva"
        elif lookup_gds1:
            vh, match_status = match_valor_hora(chave, lookup_gds1)
            if vh is not None:
                valor = vh
                origem = "🔗 GDS-1 (auto)"
            elif match_status == "ambiguo":
                origem = "⚠️ Conferir (ambíguo)"
        linhas_valor.append({
            "Cliente": r["cliente"],
            "Contrato": chave,
            "Valor/hora (R$)": valor,
            "Origem": origem,
        })

    df_valores_base = pd.DataFrame(linhas_valor)

    st.markdown("### 💰 Valores por contrato")
    st.caption(
        "Contratos casados automaticamente com o backup do GDS-1 já vêm preenchidos. "
        "Os demais ficam em R$ 0,00 — ajuste manualmente. Todos os campos são editáveis."
    )

    df_valores_editado = st.data_editor(
        df_valores_base,
        column_config={
            "Cliente": st.column_config.TextColumn(disabled=True),
            "Contrato": st.column_config.TextColumn(disabled=True),
            "Valor/hora (R$)": st.column_config.NumberColumn(min_value=0.0, step=0.01, format="R$ %.2f"),
            "Origem": st.column_config.TextColumn(disabled=True),
        },
        hide_index=True,
        use_container_width=True,
        key="editor_valores",
    )

    valor_hora_por_contrato = dict(zip(df_valores_editado["Contrato"], df_valores_editado["Valor/hora (R$)"]))

    config_export = json.dumps(valor_hora_por_contrato, ensure_ascii=False, indent=2).encode("utf-8")
    st.download_button(
        "💾 Salvar configuração de valores",
        data=config_export,
        file_name=f"config_valores_{date.today().strftime('%Y%m%d')}.json",
        mime="application/json",
        help="Baixe este arquivo e suba de volta em 'Configuração de valores salva' no próximo processamento para não digitar tudo de novo.",
    )
    st.markdown("---")

# ── preview antes de gerar ────────────────────────────────────────────────────

if not gerar:
    st.markdown("---")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Registros filtrados", f"{len(df):,}")
    c2.metric("Clientes", df["cliente"].nunique())
    c3.metric("Projetos", df["contrato"].nunique() if "contrato" in df.columns else df["nome_projeto"].nunique())
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

    horas_val = float(row["horas"]) if pd.notna(row["horas"]) else 0.0

    rec = {
        "cliente":   cliente,
        "proj":      proj,
        "cod_proj":  str(row.get("projeto", "")).strip(),
        "contrato":  str(row.get("contrato", "")).strip(),
        "gds_gdp":   get_gds_gdp_label(row),
        "atividade": ativ,
        "titulo":    titulo,
        "nome":      nome,
        "rf":        str(row.get("rf", "")).strip(),
        "data":      data_fmt,
        "data_iso":  data_iso,
        "horas":     horas_val,
        "horas_fmt": fmt_horas(row["horas"]),
        "os":        str(row.get("ordem_servico", "")).strip(),
        "tipo":      str(row.get("tipo_demanda", "")).strip(),
    }

    if incluir_valores:
        valor_hora = valor_hora_por_contrato.get(contrato_key_de(row), 0.0)
        valor_registro = round(horas_val * valor_hora, 2)
        rec["valor"] = valor_registro
        rec["valor_fmt"] = fmt_valor(valor_registro)

    records.append(rec)

total_geral  = df["horas"].sum()
total_valor_geral = sum(r.get("valor", 0.0) for r in records) if incluir_valores else 0.0
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
if incluir_valores:
    df_export["valor"] = [
        f"{r.get('valor', 0.0):.2f}".replace(".", ",") for r in records
    ]
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
    .replace("%%TEM_VALORES%%",     json.dumps(bool(incluir_valores)))
    .replace("%%TOTAL_VALOR%%",     fmt_valor(total_valor_geral))
)

# ── resultado ─────────────────────────────────────────────────────────────────

st.markdown("---")
cols_metric = st.columns(5 if incluir_valores else 4)
cols_metric[0].metric("Registros",      f"{len(df):,}")
cols_metric[1].metric("Clientes",       df["cliente"].nunique())
cols_metric[2].metric("Projetos",       df["contrato"].nunique() if "contrato" in df.columns else df["nome_projeto"].nunique())
cols_metric[3].metric("Total de horas", fmt_horas(total_geral))
if incluir_valores:
    cols_metric[4].metric("Total (R$)", fmt_valor(total_valor_geral))

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
