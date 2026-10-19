import json
import zipfile
from io import BytesIO
from pathlib import Path

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

st.set_page_config(
    page_title="전국 고령화 단계구분도",
    page_icon="🗺️",
    layout="wide",
)

# 제공된 주소는 현재 404를 반환할 수 있으므로, 아래 우선순위로 데이터를 읽습니다.
# 1) 앱 화면에서 업로드한 파일  2) main.py와 같은 폴더의 파일  3) 원격 URL
POPULATION_URL = (
    "https://raw.githubusercontent.com/MENSIIK/contents/main/"
    "population_ts_yearly_gender%20%281%29.zip"
)
GEOJSON_URL = "https://raw.githubusercontent.com/MENSIIK/contents/main/sigungu_kr.geojson"
BASE_DIR = Path(__file__).resolve().parent
LOCAL_POPULATION = BASE_DIR / "population_ts_yearly_gender(1).zip"
LOCAL_GEOJSON = BASE_DIR / "sigungu_kr.geojson"


def fetch_url(url: str, label: str) -> bytes:
    try:
        response = requests.get(url, timeout=120)
    except requests.RequestException as exc:
        raise RuntimeError(f"{label} URL에 연결하지 못했습니다: {exc}") from exc

    if response.status_code == 404:
        raise RuntimeError(
            f"{label} URL이 404(Not Found)를 반환했습니다.\n\n"
            f"주소: {url}\n\n"
            "파일이 삭제/이동되었거나 저장소·브랜치·파일명이 잘못되었을 수 있습니다. "
            "아래에서 파일을 업로드하거나, 파일을 GitHub 저장소에 함께 올려 주세요."
        )
    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"{label} 다운로드에 실패했습니다: {exc}") from exc
    return response.content


def get_file_bytes(uploaded_file, local_path: Path, url: str, label: str) -> bytes:
    if uploaded_file is not None:
        return uploaded_file.getvalue()
    if local_path.exists():
        return local_path.read_bytes()
    return fetch_url(url, label)


@st.cache_data(show_spinner=False)
def parse_population(zip_bytes: bytes) -> pd.DataFrame:
    try:
        with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
            csv_files = [
                name for name in zf.namelist()
                if name.lower().endswith(".csv") and not name.startswith("__MACOSX/")
            ]
            if not csv_files:
                raise ValueError("ZIP 파일 안에서 CSV 파일을 찾지 못했습니다.")
            csv_bytes = zf.read(csv_files[0])
    except zipfile.BadZipFile as exc:
        raise ValueError("인구 데이터가 올바른 ZIP 파일이 아닙니다.") from exc

    # UTF-8 파일을 먼저 시도하고, 실패하면 한글 CSV에서 흔한 CP949를 시도합니다.
    try:
        df = pd.read_csv(BytesIO(csv_bytes), dtype={"코드": str}, encoding="utf-8-sig")
    except UnicodeDecodeError:
        df = pd.read_csv(BytesIO(csv_bytes), dtype={"코드": str}, encoding="cp949")

    required = {"연도", "코드"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"인구 CSV에 필요한 열이 없습니다: {', '.join(sorted(missing))}")

    df["연도"] = pd.to_numeric(df["연도"], errors="coerce")
    df = df.loc[df["연도"] == 2026].copy()
    if df.empty:
        raise ValueError("인구 데이터에서 2026년 자료를 찾지 못했습니다.")

    df["코드"] = df["코드"].astype("string").str.strip()
    df["시군구코드"] = df["코드"].str[:5].str.zfill(5)

    # 남_ / 여_ 열은 계산에서 제외하고, 계_ 열만 이용합니다.
    total_cols = [col for col in df.columns if col.startswith("계_")]
    elderly_cols = []
    for col in total_cols:
        age = col.removeprefix("계_").strip()
        if age == "100세 이상":
            elderly_cols.append(col)
        elif age.endswith("세") and age[:-1].isdigit() and int(age[:-1]) >= 65:
            elderly_cols.append(col)

    if not total_cols:
        raise ValueError("'계_'로 시작하는 전체 인구 열을 찾지 못했습니다.")
    if not elderly_cols:
        raise ValueError("'계_65세'부터 '계_100세 이상'까지의 열을 찾지 못했습니다.")

    for col in total_cols:
        df[col] = pd.to_numeric(
            df[col].astype(str).str.replace(",", "", regex=False),
            errors="coerce",
        ).fillna(0)

    df["전체인구"] = df[total_cols].sum(axis=1)
    df["65세이상인구"] = df[elderly_cols].sum(axis=1)

    result = (
        df.groupby("시군구코드", as_index=False)
        .agg(전체인구=("전체인구", "sum"), 고령인구=("65세이상인구", "sum"))
    )
    result["고령화율"] = (
        result["고령인구"]
        .div(result["전체인구"].where(result["전체인구"] > 0))
        .mul(100)
    )
    return result


@st.cache_data(show_spinner=False)
def parse_geojson(geojson_bytes: bytes) -> dict:
    try:
        geojson = json.loads(geojson_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("시군구 경계 파일이 올바른 JSON/GeoJSON 형식이 아닙니다.") from exc

    features = geojson.get("features")
    if not isinstance(features, list):
        raise ValueError("GeoJSON에 features 목록이 없습니다.")

    for feature in features:
        props = feature.setdefault("properties", {})
        if props.get("코드") is not None:
            props["코드"] = str(props["코드"]).strip().zfill(5)
    return geojson


st.title("전국 고령화 단계구분도")
st.caption("2026년 자료 · 시군구별 65세 이상 인구 비율")

with st.sidebar:
    st.header("데이터 파일")
    st.caption(
        "원본 다운로드 주소에서 404 오류가 발생하면 두 파일을 여기서 업로드하세요. "
        "또는 두 파일을 GitHub 저장소에서 main.py와 같은 폴더에 올려도 됩니다."
    )
    population_upload = st.file_uploader(
        "인구 데이터 ZIP", type=["zip"], key="population_zip"
    )
    geojson_upload = st.file_uploader(
        "시군구 경계 GeoJSON", type=["geojson", "json"], key="geojson_file"
    )

try:
    with st.spinner("인구 데이터를 불러오는 중..."):
        population_bytes = get_file_bytes(
            population_upload, LOCAL_POPULATION, POPULATION_URL, "인구 데이터"
        )
        population = parse_population(population_bytes)

    with st.spinner("시군구 경계 데이터를 불러오는 중..."):
        geojson_bytes = get_file_bytes(
            geojson_upload, LOCAL_GEOJSON, GEOJSON_URL, "시군구 경계 데이터"
        )
        geojson = parse_geojson(geojson_bytes)

    boundary_codes = {
        feature["properties"]["코드"]
        for feature in geojson["features"]
        if feature.get("properties", {}).get("코드") is not None
    }
    population["시군구코드"] = population["시군구코드"].astype("string").str.zfill(5)
    map_df = population[population["시군구코드"].isin(boundary_codes)].copy()

    name_by_code = {
        feature["properties"]["코드"]: feature["properties"].get("시군구", "이름 없음")
        for feature in geojson["features"]
        if feature.get("properties", {}).get("코드") is not None
    }
    map_df["시군구"] = map_df["시군구코드"].map(name_by_code)
    map_df = map_df.dropna(subset=["고령화율"])

    if map_df.empty:
        st.error(
            "인구 데이터와 경계 데이터의 코드가 일치하지 않습니다. "
            "두 파일의 '코드' 형식 및 기준 연도를 확인해 주세요."
        )
        st.stop()

    total_population = map_df["전체인구"].sum()
    total_elderly = map_df["고령인구"].sum()
    national_rate = total_elderly / total_population * 100 if total_population > 0 else 0

    c1, c2, c3 = st.columns(3)
    c1.metric("지도에 표시된 시군구", f"{len(map_df):,}개")
    c2.metric("전체 인구", f"{total_population:,.0f}명")
    c3.metric("65세 이상 비율", f"{national_rate:.2f}%")

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
    fig.update_layout(
        height=800,
        margin=dict(l=0, r=0, t=55, b=0),
        coloraxis_colorbar=dict(title="고령화율 (%)"),
    )
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
        "전국 비율은 지도에 표시된 시군구의 인구를 합산해 계산합니다. "
        "경계 코드와 일치하지 않는 지역은 지도에서 제외됩니다."
    )

except Exception as exc:
    st.error(str(exc))
