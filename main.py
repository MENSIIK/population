
import math

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


# 페이지 기본 설정
st.set_page_config(
    page_title="동네별 인구 피라미드",
    page_icon="🏘️",
    layout="wide",
)

# 따뜻한 색감으로 화면을 꾸밉니다.
st.markdown(
    """
    <style>
    .stApp {
        background-color: #FFF9F2;
        color: #3D3029;
    }
    [data-testid="stHeader"] {
        background-color: rgba(255, 249, 242, 0.92);
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# GitHub에 있는 ZIP 압축 CSV 주소입니다.
DATA_URL = (
    "https://raw.githubusercontent.com/MENSIIK/population/main/"
    "population_ts_yearly_gender(1).zip"
)

# 0~99세와 100세 이상까지 총 101개 연령 구간입니다.
AGE_LABELS = [f"{age}세" for age in range(100)] + ["100세 이상"]
MALE_COLUMNS = [f"남_{age}" for age in AGE_LABELS]
FEMALE_COLUMNS = [f"여_{age}" for age in AGE_LABELS]


@st.cache_data(show_spinner="최신 인구 데이터를 불러오는 중입니다...")
def load_population_data():
    """압축 CSV를 읽고 가장 최신 연도 데이터만 정리합니다."""
    # URL이 .zip으로 끝나므로 pandas가 ZIP 압축을 풀어 CSV로 읽습니다.
    data = pd.read_csv(DATA_URL, compression="zip")

    # 열 이름에 BOM이나 불필요한 공백이 섞인 경우를 정리합니다.
    data.columns = [
        str(column).replace("\ufeff", "").strip()
        for column in data.columns
    ]

    required_columns = [
        "연도", "시도", "시군구", "동"
    ] + MALE_COLUMNS + FEMALE_COLUMNS

    missing_columns = [
        column
        for column in required_columns
        if column not in data.columns
    ]

    if missing_columns:
        raise ValueError(
            "CSV에서 필요한 열을 찾지 못했습니다: "
            + ", ".join(missing_columns[:10])
        )

    # 연도 열이 숫자 또는 '2026년' 같은 문자열이어도
    # 연도 숫자를 추출할 수 있도록 처리합니다.
    year_text = data["연도"].astype(str).str.extract(
        r"(\d{4})", expand=False
    )
    data["_연도_숫자"] = pd.to_numeric(
        year_text, errors="coerce"
    )

    valid_years = data["_연도_숫자"].dropna()

    if valid_years.empty:
        raise ValueError(
            "연도 열에서 유효한 연도를 찾지 못했습니다."
        )

    # 데이터에 들어 있는 연도 중 가장 큰 연도만 남깁니다.
    latest_year = int(valid_years.max())
    data = data.loc[
        data["_연도_숫자"] == latest_year
    ].copy()

    # 지역 이름의 빈칸과 결측값을 정리합니다.
    for column in ["시도", "시군구", "동"]:
        data[column] = (
            data[column].fillna("").astype(str).str.strip()
        )

    # 인구 열에 쉼표가 있거나 값이 비어 있어도
    # 숫자형으로 변환합니다.
    population_columns = MALE_COLUMNS + FEMALE_COLUMNS

    for column in population_columns:
        values = (
            data[column]
            .astype(str)
            .str.replace(",", "", regex=False)
            .str.strip()
        )

        data[column] = pd.to_numeric(
            values, errors="coerce"
        ).fillna(0)

    return data, latest_year


st.title("🏘️ 동네별 인구 피라미드")
st.write(
    "시도 → 시군구 → 읍·면·동을 차례로 선택하면, "
    "해당 지역의 연령별 남녀 인구를 가로 막대그래프로 "
    "확인할 수 있습니다."
)

try:
    population_df, latest_year = load_population_data()

except Exception as error:
    st.error(
        "인구 데이터를 불러오지 못했습니다. "
        "데이터 주소나 CSV 열 구성을 확인해 주세요."
    )
    st.exception(error)
    st.stop()

st.caption(
    f"기준 연도: {latest_year}년 · "
    "데이터: GitHub 인구 통계 CSV"
)

# 첫 번째 드롭다운: 시도
sido_options = sorted(
    value
    for value in population_df["시도"].unique()
    if value
)

if not sido_options:
    st.error("선택할 수 있는 시도 데이터가 없습니다.")
    st.stop()

sido_col, sigungu_col, dong_col = st.columns(3)

with sido_col:
    selected_sido = st.selectbox(
        "① 시도",
        sido_options,
        key="sido_select",
    )

# 선택한 시도에 해당하는 행만 모아
# 두 번째 드롭다운의 선택지를 만듭니다.
sido_df = population_df.loc[
    population_df["시도"] == selected_sido
]

sigungu_options = sorted(
    sido_df["시군구"].drop_duplicates().tolist()
)

if not sigungu_options:
    st.error("선택한 시도에 시군구 데이터가 없습니다.")
    st.stop()

with sigungu_col:
    selected_sigungu = st.selectbox(
        "② 시군구",
        sigungu_options,
        format_func=lambda value: (
            value if value else "시군구 없음"
        ),
        # 시도를 바꾸면 해당 시도의 메뉴를 사용합니다.
        key=f"sigungu_{selected_sido}",
    )

# 선택한 시군구 안에서 실제 읍·면·동 이름만 추립니다.
sigungu_df = sido_df.loc[
    sido_df["시군구"] == selected_sigungu
]

dong_options = sorted(
    value
    for value in sigungu_df["동"].drop_duplicates().tolist()
    if value
)

if not dong_options:
    st.warning(
        "선택한 시군구에서 읍·면·동 단위 데이터를 "
        "찾지 못했습니다."
    )
    st.stop()

with dong_col:
    selected_dong = st.selectbox(
        "③ 읍·면·동",
        dong_options,
        # 시군구를 바꾸면 해당 지역의 읍·면·동 메뉴를 사용합니다.
        key=f"dong_{selected_sido}_{selected_sigungu}",
    )

# 세 드롭다운에서 선택한 지역과 일치하는 행을 찾습니다.
selected_rows = population_df.loc[
    (population_df["시도"] == selected_sido)
    & (population_df["시군구"] == selected_sigungu)
    & (population_df["동"] == selected_dong)
]

if selected_rows.empty:
    st.error("선택한 지역의 인구 데이터를 찾지 못했습니다.")
    st.stop()

# 같은 지역이 여러 행으로 들어온 경우에는 연령별 인구를 합산합니다.
selected_population = selected_rows[
    MALE_COLUMNS + FEMALE_COLUMNS
].sum(axis=0)

male_values = [
    int(selected_population[column])
    for column in MALE_COLUMNS
]

female_values = [
    int(selected_population[column])
    for column in FEMALE_COLUMNS
]

male_total = sum(male_values)
female_total = sum(female_values)
total_population = male_total + female_total

# 선택한 지역의 요약 수치를 표시합니다.
metric_total, metric_male, metric_female = st.columns(3)

metric_total.metric(
    "총인구", f"{total_population:,}명"
)
metric_male.metric(
    "남자 인구", f"{male_total:,}명"
)
metric_female.metric(
    "여자 인구", f"{female_total:,}명"
)

# 좌우 축 범위가 같도록 설정합니다.
max_population = max(
    max(male_values, default=0),
    max(female_values, default=0),
    1,
)

axis_limit = max(
    100,
    int(math.ceil(max_population / 100) * 100),
)

tick_values = [
    -axis_limit,
    -axis_limit / 2,
    0,
    axis_limit / 2,
    axis_limit,
]

# 남자 인구는 그래프 내부에서 음수지만,
# 눈금 숫자는 절댓값으로 표시합니다.
tick_text = [
    f"{abs(int(round(value))):,}"
    for value in tick_values
]

figure = go.Figure()

# 남자 인구는 음수로 바꾸어 중앙선 왼쪽에 그립니다.
figure.add_trace(
    go.Bar(
        x=[-value for value in male_values],
        y=AGE_LABELS,
        orientation="h",
        name="남자",
        marker_color="#C66D4E",
        customdata=male_values,
        hovertemplate=(
            "나이: %{y}<br>"
            "남자 인구: %{customdata:,.0f}명"
            "<extra></extra>"
        ),
    )
)

# 여자 인구는 양수로 두어 중앙선 오른쪽에 그립니다.
figure.add_trace(
    go.Bar(
        x=female_values,
        y=AGE_LABELS,
        orientation="h",
        name="여자",
        marker_color="#E6B36A",
        customdata=female_values,
        hovertemplate=(
            "나이: %{y}<br>"
            "여자 인구: %{customdata:,.0f}명"
            "<extra></extra>"
        ),
    )
)

figure.update_layout(
    title={
        "text": (
            f"{selected_sido} {selected_sigungu} "
            f"{selected_dong} 인구 피라미드"
        ),
        "x": 0.5,
        "xanchor": "center",
    },
    barmode="relative",
    bargap=0.08,
    height=1500,
    paper_bgcolor="#FFF9F2",
    plot_bgcolor="#FFF9F2",
    font={
        "family": "Arial, sans-serif",
        "color": "#3D3029",
        "size": 12,
    },
    legend={
        "orientation": "h",
        "yanchor": "bottom",
        "y": 1.02,
        "xanchor": "center",
        "x": 0.5,
    },
    margin={
        "l": 80,
        "r": 35,
        "t": 100,
        "b": 65,
    },
    hovermode="y unified",
)

figure.update_xaxes(
    title_text="인구수 (명)",
    range=[-axis_limit, axis_limit],
    tickmode="array",
    tickvals=tick_values,
    ticktext=tick_text,
    showgrid=True,
    gridcolor="#EADFD4",
    zeroline=True,
    zerolinecolor="#8A766A",
    zerolinewidth=1.5,
    ticks="outside",
)

# 중요: 나이 라벨을 0세부터 100세 이상까지 순서대로 고정합니다.
# 세로 범주축에서는 첫 항목이 아래쪽, 마지막 항목이 위쪽에 놓입니다.
# 따라서 0세가 맨 아래, 100세 이상이 맨 위에 표시됩니다.
# 축 반전이나 인구수 기준 정렬은 사용하지 않습니다.
figure.update_yaxes(
    title_text="나이",
    categoryorder="array",
    categoryarray=AGE_LABELS,
    tickmode="array",
    tickvals=AGE_LABELS,
    ticktext=AGE_LABELS,
    autorange=True,
    showgrid=False,
    tickfont={"size": 10},
    ticks="outside",
)

st.plotly_chart(figure, use_container_width=True)

st.caption(
    "그래프 중앙을 기준으로 남자 인구는 왼쪽, "
    "여자 인구는 오른쪽에 표시됩니다."
)
