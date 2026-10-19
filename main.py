
import json
import zipfile
from io import BytesIO

import pandas as pd
import requests
import plotly.express as px
import streamlit as st


st.set_page_config(
    page_title="전국 고령화 단계구분도",
    page_icon="🗺️",
    layout="wide",
)

POPULATION_URL = (
    "https://raw.githubusercontent.com/MENSIIK/contents//main/"
    "population_ts_yearly_gender(1).zip"
)
GEOJSON_URL = (
    "https://raw.githubusercontent.com/MENSIIK/contents//main/"
    "sigungu_kr.geojson"
)


@st.cache_data(show_spinner="2026년 인구 데이터를 불러오는 중...")
def load_population():
    response = requests.get(POPULATION_URL, timeout=120)
    response.raise_for_status()

    # 압축 CSV를 메모리에서 직접 읽기
    with zipfile.ZipFile(BytesIO(response.content)) as zf:
        csv_files = [
            name for name in zf.namelist()
            if name.lower().endswith(".csv")
        ]
        if not csv_files:
            raise ValueError("ZIP 파일 안에서 CSV 파일을 찾지 못했습니다.")

        with zf.open(csv_files[0]) as file:
            df = pd.read_csv(file, dtype={"코드": str})

    # 연도는 숫자로 통일한 뒤 2026년 자료만 선택
    df["연도"] = pd.to_numeric(df["연도"], errors="coerce")
    df = df.loc[df["연도"] == 2026].copy()

    if df.empty:
        raise ValueError("인구 데이터에서 2026년 자료를 찾지 못했습니다.")

    df["코드"] = df["코드"].astype("string").str.strip()
    df["시군구코드"] = df["코드"].str[:5]

    # '계_'로 시작하는 열만 사용하여 전체 인구 계산
    total_cols = [
        col for col in df.columns
        if col.startswith("계_")
    ]

    # 65세부터 99세까지와 100세 이상 포함
    elderly_cols = [
        col for col in total_cols
        if col.removeprefix("계_") == "100세 이상"
        or (
            col.removeprefix("계_").endswith("세")
            and col.removeprefix("계_")[:-1].isdigit()
            and int(col.removeprefix("계_")[:-1]) >= 65
        )
    ]

    if not total_cols or not elderly_cols:
        raise ValueError(
            "전체 인구 또는 65세 이상 인구 열을 찾지 못했습니다."
        )

    # 쉼표 등이 포함된 숫자 문자열도 처리
    for col in total_cols:
        df[col] = pd.to_numeric(
            df[col].astype(str).str.replace(",", "", regex=False),
            errors="coerce",
        ).fillna(0)

    # 동별 인구를 시군구 코드 기준으로 합산
    df["전체인구"] = df[total_cols].sum(axis=1)
    df["65세이상인구"] = df[elderly_cols].sum(axis=1)

    result = (
        df.groupby("시군구코드", as_index=False)
        .agg(
            전체인구=("전체인구", "sum"),
            고령인구=("65세이상인구", "sum"),
        )
    )

    result["고령화율"] = (
        result["고령인구"]
        .div(result["전체인구"].where(result["전체인구"] > 0))
        .mul(100)
    )

    return result


@st.cache_data(show_spinner="시군구 경계 데이터를 불러오는 중...")
def load_geojson():
    response = requests.get(GEOJSON_URL, timeout=120)
    response.raise_for_status()
    geojson = response.json()

    # GeoJSON 코드도 문자열로 통일
    for feature in geojson.get("features", []):
        properties = feature.get("properties", {})
        if properties.get("코드") is not None:
            properties["코드"] = str(properties["코드"]).strip().zfill(5)

    return geojson


st.title("전국 고령화 단계구분도")
st.caption("2026년 6월 기준 · 시군구별 65세 이상 인구 비율")

try:
    population = load_population()
    geojson = load_geojson()

    # 경계에 실제로 존재하는 시군구 코드만 지도에 표시
    boundary_codes = {
        feature["properties"]["코드"]
        for feature in geojson.get("features", [])
        if feature.get("properties", {}).get("코드") is not None
    }

    population["시군구코드"] = (
        population["시군구코드"].astype("string").str.zfill(5)
    )

    map_df = population[
        population["시군구코드"].isin(boundary_codes)
    ].copy()

    # 지도에 표시할 이름은 경계 GeoJSON의 속성을 사용
    name_by_code = {
        feature["properties"]["코드"]: feature["properties"].get(
            "시군구", "이름 없음"
        )
        for feature in geojson.get("features", [])
        if feature.get("properties", {}).get("코드") is not None
    }

    map_df["시군구"] = map_df["시군구코드"].map(name_by_code)
    map_df = map_df.dropna(subset=["고령화율"])

    if map_df.empty:
        st.error(
            "인구 데이터와 경계 데이터의 코드가 일치하지 않습니다. "
            "두 파일의 코드 형식을 확인해 주세요."
        )
        st.stop()

    # 요약 지표
    total_population = map_df["전체인구"].sum()
    total_elderly = map_df["고령인구"].sum()
    national_rate = (
        total_elderly / total_population * 100
        if total_population > 0 else 0
    )

    col1, col2, col3 = st.columns(3)
    col1.metric("지도에 표시된 시군구", f"{len(map_df):,}개")
    col2.metric("전체 인구", f"{total_population:,.0f}명")
    col3.metric("65세 이상 비율", f"{national_rate:.2f}%")

    fig = px.choropleth(
        map_df,
        geojson=geojson,
        locations="시군구코드",
        featureidkey="properties.코드",
        color="고령화율",
        color_continuous_scale=[
            "#fff5eb",
            "#fdd0a2",
            "#f16913",
            "#a63603",
            "#7f0000",
        ],
        hover_name="시군구",
        hover_data={
            "시군구코드": False,
            "고령화율": ":.2f",
        },
        labels={"고령화율": "고령화율 (%)"},
        title="시군구별 65세 이상 인구 비율",
    )

    # 배경 지도와 타일 없이 경계만 표시
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
        hovertemplate=(
            "<b>%{hovertext}</b><br>"
            "고령화율: %{z:.2f}%"
            "<extra></extra>"
        ),
    )

    fig.update_layout(
        height=800,
        margin=dict(l=0, r=0, t=55, b=0),
        coloraxis_colorbar=dict(title="고령화율 (%)"),
    )

    st.plotly_chart(fig, use_container_width=True)

    with st.expander("시군구별 수치 확인"):
        table = map_df[
            ["시군구코드", "시군구", "전체인구", "고령인구", "고령화율"]
        ].copy()
        table = table.sort_values("고령화율", ascending=False)
        table = table.rename(columns={
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
        "참고: 전국 비율은 지도에 표시된 시군구의 인구를 합산해 계산합니다. "
        "경계 코드와 일치하지 않는 지역은 지도에서 제외됩니다."
    )

except Exception as e:
    st.error(f"데이터를 불러오거나 지도를 생성하지 못했습니다: {e}")
    st.info(
        "데이터 URL에 접근할 수 있는지, ZIP 내부에 CSV가 있는지, "
        "필요한 라이브러리가 설치되어 있는지 확인해 주세요."
    )
