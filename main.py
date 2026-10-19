import json
import re
import zipfile
from io import BytesIO
from pathlib import Path

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

st.set_page_config(page_title="전국 고령화 단계구분도", page_icon="🗺️", layout="wide")

# 기존 MENSIIK/contents 주소는 저장소 자체가 404이므로, 현재 접근 가능한 공개 자료원을 사용합니다.
POPULATION_URL = "https://raw.githubusercontent.com/rainymon/2026-pop/main/202606_202606.csv"
GEOJSON_URL = "https://raw.githubusercontent.com/KnellBalm/kr-admin-geojson/main/sig.geojson"
BASE_DIR = Path(__file__).resolve().parent


def download_bytes(url: str, label: str) -> bytes:
    try:
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        return response.content
    except requests.RequestException as exc:
        raise RuntimeError(f"{label} 다운로드에 실패했습니다.\n주소: {url}\n원인: {exc}") from exc


def read_csv_bytes(raw: bytes) -> pd.DataFrame:
    """CSV는 UTF-8 계열을 먼저 시도하고, 실패하면 행정안전부 CSV에서 흔한 CP949를 시도합니다."""
    errors = []
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            header = pd.read_csv(BytesIO(raw), nrows=0, encoding=encoding)
            dtype = {"코드": str} if "코드" in header.columns else None
            return pd.read_csv(
                BytesIO(raw),
                encoding=encoding,
                dtype=dtype,
                low_memory=False,
            )
        except (UnicodeDecodeError, pd.errors.ParserError, LookupError) as exc:
            errors.append(f"{encoding}: {exc}")
    raise ValueError("CSV 파일을 읽을 수 없습니다. 인코딩 또는 파일 형식을 확인해 주세요. " + " | ".join(errors))


def read_population_table(raw: bytes) -> pd.DataFrame:
    """ZIP 안의 CSV와 일반 CSV를 모두 지원합니다."""
    if zipfile.is_zipfile(BytesIO(raw)):
        with zipfile.ZipFile(BytesIO(raw)) as zf:
            csv_files = [
                name for name in zf.namelist()
                if name.lower().endswith(".csv") and not name.startswith("__MACOSX/")
            ]
            if not csv_files:
                raise ValueError("압축 파일 내부에서 CSV 파일을 찾지 못했습니다.")
            raw = zf.read(csv_files[0])
    return read_csv_bytes(raw)


def get_age(col_name: str):
    """계_0세, 계_65세, 계_100세 이상, 2026년06월_계_0세 형식을 인식합니다."""
    name = str(col_name).strip()
    match = re.search(r"(?:^|_)계_(\d+)\s*세\s*$", name)
    if match:
        return int(match.group(1))
    if re.search(r"(?:^|_)계_100\s*세\s*이상\s*$", name):
        return 100
    return None


def choose_age_columns(columns):
    age_columns = [(col, get_age(col)) for col in columns]
    age_columns = [(col, age) for col, age in age_columns if age is not None]
    if not age_columns:
        raise ValueError("연령별 전체 인구 열(계_0세, 계_1세 등)을 찾지 못했습니다.")

    # 대체 CSV는 열 이름에 '2026년06월_' 접두사가 있습니다. 2026년 6월 열만 선택합니다.
    dated = []
    for col, age in age_columns:
        match = re.match(r"^(\d{4})년\s*(\d{1,2})월", str(col).strip())
        if match:
            dated.append((col, age, int(match.group(1)), int(match.group(2))))
    if dated:
        june_2026 = [(col, age) for col, age, year, month in dated if year == 2026 and month == 6]
        if june_2026:
            return june_2026
        year_2026 = [(col, age) for col, age, year, _ in dated if year == 2026]
        if year_2026:
            return year_2026
        raise ValueError("CSV의 연령 열에서 2026년 자료를 찾지 못했습니다.")
    return age_columns


def normalise_code(value) -> str:
    if pd.isna(value):
        return ""
    code = str(value).strip()
    if code.endswith(".0"):
        code = code[:-2]
    digits = re.sub(r"\D", "", code)
    return digits


@st.cache_data(show_spinner=False)
def calculate_population(raw: bytes) -> pd.DataFrame:
    df = read_population_table(raw)

    # 이전 형식은 연도 열에 2015~2026년이 있으므로 2026년 행만 사용합니다.
    if "연도" in df.columns:
        years = pd.to_numeric(df["연도"], errors="coerce")
        df = df.loc[years == 2026].copy()
        if df.empty:
            raise ValueError("인구 데이터에서 2026년 자료를 찾지 못했습니다.")

    age_column_pairs = choose_age_columns(df.columns)
    age_cols = [col for col, _ in age_column_pairs]
    elderly_cols = [col for col, age in age_column_pairs if age >= 65]
    if not elderly_cols:
        raise ValueError("65세 이상부터 100세 이상까지의 인구 열이 없습니다.")

    # 원래 형식은 '코드' 열, 대체 주민등록 CSV는 행정구역 문자열 끝의 10자리 코드를 사용합니다.
    if "코드" in df.columns:
        codes = df["코드"].map(normalise_code)
        # 시도·시군구·동·코드 구조는 이미 읍면동 행만 담고 있으므로 모든 행을 사용합니다.
        structured_dong_table = {"시도", "시군구", "동"}.issubset(df.columns)
    elif "행정구역" in df.columns:
        codes = df["행정구역"].astype(str).str.extract(r"(\d{5,10})\s*\)?\s*$", expand=False).fillna("")
        codes = codes.map(normalise_code)
        structured_dong_table = False
    else:
        raise ValueError("CSV에 '코드' 열 또는 '행정구역' 열이 없습니다.")

    df["__code"] = codes
    df["__code"] = df["__code"].astype(str).str.strip()
    df = df.loc[df["__code"].str.len().ge(5)].copy()

    # 통합 주민등록 CSV에는 시도·시군구·읍면동 행이 함께 들어 있습니다.
    # 10자리 코드의 뒤 5자리가 00000이면 시군구 합계 행이므로 제외합니다.
    if not structured_dong_table:
        ten_digit = df["__code"].str.len().eq(10)
        dong_rows = ten_digit & df["__code"].str[5:].ne("00000")
        df = df.loc[dong_rows].copy()
        if df.empty:
            raise ValueError("CSV에서 읍·면·동 단위 10자리 행정구역 코드를 찾지 못했습니다.")

    for col in age_cols:
        df[col] = pd.to_numeric(
            df[col].astype(str).str.replace(",", "", regex=False),
            errors="coerce",
        ).fillna(0)

    # 나이별 '계_' 열만 합산합니다. 남성·여성 열과 별도의 총인구 열은 중복 합산하지 않습니다.
    df["전체인구"] = df[age_cols].sum(axis=1)
    df["고령인구"] = df[elderly_cols].sum(axis=1)
    df["시군구코드"] = df["__code"].str[:5].str.zfill(5)

    result = (
        df.groupby("시군구코드", as_index=False)
        .agg(전체인구=("전체인구", "sum"), 고령인구=("고령인구", "sum"))
    )
    result["고령화율"] = (
        result["고령인구"]
        .div(result["전체인구"].where(result["전체인구"] > 0))
        .mul(100)
    )
    result = result.dropna(subset=["고령화율"])
    if result.empty:
        raise ValueError("시군구별 고령화율을 계산할 수 있는 행이 없습니다.")
    return result


@st.cache_data(show_spinner=False)
def normalise_geojson(raw: bytes) -> dict:
    try:
        geojson = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("시군구 경계 파일이 올바른 GeoJSON이 아닙니다.") from exc

    features = geojson.get("features")
    if not isinstance(features, list):
        raise ValueError("GeoJSON에 features 목록이 없습니다.")

    for feature in features:
        props = feature.setdefault("properties", {})
        # 대체 GeoJSON의 속성명(SIG_CD, SIG_KOR_NM, SIDO_NM)을 표준 속성으로 맞춥니다.
        code = props.get("코드", props.get("SIG_CD"))
        if code is not None:
            props["코드"] = normalise_code(code).zfill(5)
        if not props.get("시군구"):
            props["시군구"] = props.get("SIG_KOR_NM", props.get("FULL_NM", "이름 없음"))
        if not props.get("시도"):
            props["시도"] = props.get("SIDO_NM", "")
    return geojson


st.title("전국 고령화 단계구분도")
st.caption("2026년 6월 기준 · 시군구별 65세 이상 인구 비율")

with st.sidebar:
    st.header("직접 파일 업로드(선택)")
    st.caption("자동 다운로드가 실패할 때만 파일을 직접 넣으면 됩니다.")
    population_upload = st.file_uploader("인구 데이터 ZIP 또는 CSV", type=["zip", "csv"])
    geojson_upload = st.file_uploader("시군구 경계 GeoJSON", type=["geojson", "json"])

try:
    with st.spinner("2026년 인구 데이터를 불러오는 중..."):
        if population_upload is not None:
            population_bytes = population_upload.getvalue()
        else:
            local_candidates = [
                BASE_DIR / "population_ts_yearly_gender(1).zip",
                BASE_DIR / "202606_202606.csv",
                BASE_DIR / "202606_202606_연령별인구현황_월간.csv",
            ]
            local_file = next((p for p in local_candidates if p.is_file()), None)
            population_bytes = local_file.read_bytes() if local_file else download_bytes(POPULATION_URL, "인구 데이터")
        population = calculate_population(population_bytes)

    with st.spinner("시군구 경계 데이터를 불러오는 중..."):
        if geojson_upload is not None:
            geojson_bytes = geojson_upload.getvalue()
        else:
            local_geojsons = [BASE_DIR / "sigungu_kr.geojson", BASE_DIR / "sig.geojson"]
            local_geojson = next((p for p in local_geojsons if p.is_file()), None)
            geojson_bytes = local_geojson.read_bytes() if local_geojson else download_bytes(GEOJSON_URL, "시군구 경계")
        geojson = normalise_geojson(geojson_bytes)

    name_by_code = {}
    boundary_codes = set()
    for feature in geojson["features"]:
        props = feature.get("properties", {})
        code = props.get("코드")
        if code:
            boundary_codes.add(code)
            name_by_code[code] = props.get("시군구", "이름 없음")

    map_df = population[population["시군구코드"].isin(boundary_codes)].copy()
    map_df["시군구"] = map_df["시군구코드"].map(name_by_code)
    if map_df.empty:
        st.error(
            "인구 데이터와 시군구 경계 코드가 하나도 일치하지 않습니다. "
            "인구 CSV의 행정구역 코드가 10자리인지 확인해 주세요."
        )
        st.stop()

    national_population = map_df["전체인구"].sum()
    national_elderly = map_df["고령인구"].sum()
    national_rate = national_elderly / national_population * 100 if national_population else 0

    m1, m2, m3 = st.columns(3)
    m1.metric("지도에 표시된 시군구", f"{len(map_df):,}개")
    m2.metric("전체 인구", f"{national_population:,.0f}명")
    m3.metric("65세 이상 비율", f"{national_rate:.2f}%")

    fig = px.choropleth(
        map_df,
        geojson=geojson,
        locations="시군구코드",
        featureidkey="properties.코드",
        color="고령화율",
        color_continuous_scale=["#fff5eb", "#fdd0a2", "#f16913", "#a63603", "#7f0000"],
        hover_name="시군구",
        hover_data={"시군구코드": False, "고령화율": ":.2f"},
        labels={"고령화율": "고령화율 (%)"},
        title="시군구별 65세 이상 인구 비율",
    )
    fig.update_geos(
        visible=False,
        fitbounds="locations",
        showcountries=False,
        showcoastlines=False,
        showland=False,
        showocean=False,
        showlakes=False,
        showframe=False,
    )
    fig.update_traces(
        marker_line_color="#777777",
        marker_line_width=0.35,
        hovertemplate="<b>%{hovertext}</b><br>고령화율: %{z:.2f}%<extra></extra>",
    )
    fig.update_layout(height=800, margin=dict(l=0, r=0, t=55, b=0), coloraxis_colorbar=dict(title="고령화율 (%)"))
    st.plotly_chart(fig, use_container_width=True)

    with st.expander("시군구별 수치 확인"):
        table = map_df[["시군구코드", "시군구", "전체인구", "고령인구", "고령화율"]].copy()
        table = table.sort_values("고령화율", ascending=False).rename(columns={
            "시군구코드": "코드",
            "전체인구": "전체 인구",
            "고령인구": "65세 이상 인구",
            "고령화율": "고령화율 (%)",
        })
        st.dataframe(
            table.style.format({
                "전체 인구": "{:,.0f}",
                "65세 이상 인구": "{:,.0f}",
                "고령화율 (%)": "{:.2f}",
            }),
            use_container_width=True,
            hide_index=True,
        )

    st.caption(
        "인구 자료: 2026년 6월 주민등록 연령별 인구 CSV. "
        "경계 자료의 코드와 일치하는 시군구만 지도에 표시합니다."
    )

except Exception as exc:
    st.error(f"데이터를 불러오거나 지도를 생성하지 못했습니다: {exc}")
    st.info(
        "자동 다운로드에 실패한 경우, 사이드바에서 2026년 인구 ZIP/CSV와 "
        "시군구 경계 GeoJSON을 업로드하거나 두 파일을 main.py와 같은 GitHub 폴더에 넣어 주세요."
    )
