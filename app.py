import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle
from pypdf import PdfReader, PdfWriter
import io

# --- 페이지 설정 ---
st.set_page_config(page_title="GLP 어류순화기록서 출력", layout="wide")
st.title("🖨️ GLP 어류순화기록서(F01) 통합 출력 시스템")

# --- 1. 구글 연결 설정 ---
@st.cache_resource
def get_google_services():
    scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    # st.secrets에서 정보 읽기 (배포용)
    creds_dict = st.secrets["gcp_service_account"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
    client = gspread.authorize(creds)
    drive_service = build('drive', 'v3', credentials=creds)
    return client, drive_service

# --- 2. 이미지 다운로드 함수 ---
def download_image_from_drive(drive_service, filename_path):
    try:
        if "/" in filename_path:
            real_filename = filename_path.split("/")[-1]
        else:
            real_filename = filename_path
        if not real_filename: return None
        query = f"name = '{real_filename}' and trashed = false"
        results = drive_service.files().list(q=query, fields="files(id, name)").execute()
        files = results.get('files', [])
        if not files: return None
        file_id = files[0]['id']
        file_content = drive_service.files().get_media(fileId=file_id).execute()
        return io.BytesIO(file_content)
    except: return None

# --- 메인 로직 ---
try:
    client, drive_service = get_google_services()
    sh = client.open("어류급성독성시험시트") 
    
    # [1] 기록 데이터 로드
    ws_log = sh.worksheet("[F01] 어류순화기록서") 
    df_log = pd.DataFrame(ws_log.get_all_records()).fillna("")

    # [2] 마감 정보 로드
    try:
        ws_close = sh.worksheet("[F01] 마감정보")
        df_close = pd.DataFrame(ws_close.get_all_records()).fillna("")
    except: df_close = pd.DataFrame() 

    # [3] 정정 기록 로드 (★추가된 부분)
    try:
        ws_audit = sh.worksheet("[F01] 정정기록")
        df_audit = pd.DataFrame(ws_audit.get_all_records()).fillna("")
    except: df_audit = pd.DataFrame()

    st.success("✅ 시스템 연결 성공!")

    if not df_log.empty:
        test_ids = df_log['시험번호'].unique()
        
        # URL 파라미터 처리
        query_params = st.query_params
        target_id = query_params.get("id", None)
        
        selected_test = None
        if target_id and target_id in test_ids:
            st.info(f"🔗 요청된 시험번호: **{target_id}**")
            selected_test = target_id
        else:
            selected_test = st.selectbox("출력할 시험번호 선택", test_ids)
        
        if selected_test:
            # 1. 기록 데이터 필터링
            filtered_df = df_log[df_log['시험번호'] == selected_test]
            try:
                filtered_df['일차_정렬용'] = pd.to_numeric(filtered_df['일차'])
                filtered_df = filtered_df.sort_values(by='일차_정렬용')
            except: filtered_df = filtered_df.sort_values(by='일차')

            # 2. 마감 정보 찾기
            close_info = {}
            if not df_close.empty:
                try:
                    df_close['시험번호_str'] = df_close['시험번호'].astype(str).str.strip()
                    target_test_str = str(selected_test).strip()
                    closing_row = df_close[df_close['시험번호_str'] == target_test_str]
                    if not closing_row.empty: close_info = closing_row.iloc[-1]
                except: pass

            # 3. 정정 기록 찾기 (★추가된 부분)
            audit_records = pd.DataFrame()
            if not df_audit.empty:
                try:
                    df_audit['시험번호_str'] = df_audit['시험번호'].astype(str).str.strip()
                    audit_records = df_audit[df_audit['시험번호_str'] == str(selected_test).strip()]
                    # 날짜순 정렬
                    if not audit_records.empty:
                         audit_records = audit_records.sort_values(by='정정일시')
                except: pass

            # 화면에 데이터 표시
            st.dataframe(filtered_df)
            
            with st.expander(f"📝 정정 기록 (Audit Trail) 확인: 총 {len(audit_records)}건"):
                if not audit_records.empty:
                    st.dataframe(audit_records)
                else:
                    st.caption("수정 이력이 없습니다.")
            
            st.divider()

            if st.button("📄 통합 PDF 생성하기", type="primary"):
                try:
                    pdfmetrics.registerFont(TTFont('Malgun', 'malgun.ttf'))
                    packet = io.BytesIO()
                    # 1페이지 캔버스 시작
                    can = canvas.Canvas(packet, pagesize=(595.27, 841.89))
                    can.setFont('Malgun', 10)

                    # ==========================================
                    # [PAGE 1] 메인 기록서 (기존 코드 유지)
                    # ==========================================
                    header_row = filtered_df.iloc[0]
                    can.drawString(485, 749, str(header_row.get('시험년도', ''))) 
                    can.drawString(125, 725, str(header_row['시험번호']))
                    can.drawString(125, 670, str(header_row.get('순화장소', '')))
                    can.drawString(310, 660, str(header_row.get('관리번호', '')))

                    test_type = str(header_row.get('시험내용', '')).strip()
                    can.setFont("Helvetica-Bold", 12)
                    if "한계" in test_type: can.drawString(307, 726, "V")
                    if "농도설정" in test_type: can.drawString(353, 726, "V")
                    if "본" in test_type: can.drawString(420, 726, "V")
                    
                    species = str(header_row.get('시험종', '')).strip()
                    if "제브라피쉬" in species: can.drawString(309, 682, "V")
                    elif "잉어" in species: can.drawString(386, 682, "V")
                    elif "미꾸리" in species: can.drawString(432, 682, "V")
                    can.setFont('Malgun', 10)

                    # 표 데이터 입력
                    start_y = 593; row_height = 21.5   
                    x_day=71; x_date=105; x_feed=145; x_dead=181; x_count=218; 
                    x_temp=260; x_ph=300; x_do=339; x_water=374.5; x_note=395; x_sign=500

                    for i, (_, row) in enumerate(filtered_df.iterrows()):
                        current_y = start_y - (i * row_height)
                        can.drawCentredString(x_day, current_y, str(row['일차']))
                        
                        date_val = str(row.get('작성일시', '')).split(" ")[0]
                        if len(date_val) > 5: date_val = date_val[5:].replace("-", "/")
                        can.drawCentredString(x_date, current_y, date_val)
                        can.drawCentredString(x_temp, current_y, str(row['수온']))
                        can.drawCentredString(x_ph,   current_y, str(row['pH']))
                        can.drawCentredString(x_do,   current_y, str(row['DO']))
                        if '치사수' in row: can.drawCentredString(x_dead, current_y, str(row['치사수']))
                        if '개체수' in row: can.drawCentredString(x_count, current_y, str(row['개체수']))

                        feed_val = str(row.get('급이여부', '')).strip()
                        if feed_val == "TetraMin" or feed_val == "1": feed_mark = "①"
                        elif feed_val == "Artemia" or feed_val == "2": feed_mark = "②"
                        elif feed_val == "혼합" or feed_val == "1,2": feed_mark = "①,②"
                        elif feed_val.upper() == "TRUE": feed_mark = "O"
                        else: feed_mark = ""
                        can.drawCentredString(x_feed, current_y, feed_mark)

                        if str(row.get('환수여부', '')).upper() == "TRUE":
                            can.setFont("Helvetica-Bold", 10)
                            can.drawCentredString(x_water, current_y, "v")
                            can.setFont('Malgun', 10)

                        note_val = str(row.get('비고', ''))
                        if note_val and note_val.lower() != 'nan':
                            can.setFont('Malgun', 8)
                            can.drawString(x_note, current_y, note_val)
                            can.setFont('Malgun', 10)

                        sign_path = str(row.get('작성자_서명', '')).strip()
                        if sign_path:
                            img_data = download_image_from_drive(drive_service, sign_path)
                            if img_data:
                                try:
                                    img = ImageReader(img_data)
                                    can.drawImage(img, x_sign - 20, current_y - 5, width=40, height=20, mask='auto')
                                except: pass

                    # 하단 정보
                    try:
                        total_dead = pd.to_numeric(filtered_df['치사수'], errors='coerce').fillna(0).sum()
                        initial_count = pd.to_numeric(filtered_df['개체수'], errors='coerce').fillna(0).max()
                        mortality_rate = (total_dead / initial_count * 100) if initial_count > 0 else 0
                    except: mortality_rate = 0

                    can.setFont("Helvetica-Bold", 12)
                    y_rate = 297; y_judge = 270
                    if mortality_rate == 0:
                        can.drawString(145, y_rate, "V"); can.drawString(145, y_judge, "V")
                    elif 0 < mortality_rate < 5:
                        can.drawString(195, y_rate, "V"); can.drawString(145, y_judge, "V")
                    elif 5 <= mortality_rate <= 10:
                        can.drawString(259, y_rate, "V"); can.drawString(189, y_judge, "V")
                    else:
                        can.drawString(342, y_rate, "V"); can.drawString(233, y_judge, "V")
                    can.setFont('Malgun', 10)

                    last_row = filtered_df.iloc[-1]
                    can.drawString(150, 150, str(last_row.get('사용기기', '')))
                    can.drawString(225, 210, str(last_row.get('기기관리번호', '')))

                    y_manager = 167; x_m_date = 250; x_m_name = 370; x_m_sign = 430
                    y_verifier = 150; x_v_date = 250; x_v_name = 370; x_v_sign = 430

                    if len(close_info) > 0:
                        can.drawCentredString(x_m_date, y_manager, str(close_info.get('담당자_서명일', '')))
                        can.drawCentredString(x_m_name, y_manager, str(close_info.get('담당자_이름', '')))
                        m_path = str(close_info.get('담당자_서명', '')).strip()
                        if m_path:
                            img_data = download_image_from_drive(drive_service, m_path)
                            if img_data:
                                try:
                                    img = ImageReader(img_data)
                                    can.drawImage(img, x_m_sign - 20, y_manager - 5, width=40, height=20, mask='auto')
                                except: pass

                        if str(close_info.get('확인자_서명', '')).strip():
                            can.drawCentredString(x_v_date, y_verifier, str(close_info.get('확인자_서명일', '')))
                            can.drawCentredString(x_v_name, y_verifier, str(close_info.get('확인자_이름', '')))
                            v_path = str(close_info.get('확인자_서명', '')).strip()
                            if v_path:
                                img_data = download_image_from_drive(drive_service, v_path)
                                if img_data:
                                    try:
                                        img = ImageReader(img_data)
                                        can.drawImage(img, x_v_sign - 20, y_verifier - 5, width=40, height=20, mask='auto')
                                    except: pass
                    else:
                        can.setFont('Malgun', 8)
                        can.drawString(x_m_date, y_manager, "(마감 전)")
                        can.setFont('Malgun', 10)

                    can.showPage() # 1페이지 종료 및 저장

                    # ==========================================
                    # [PAGE 2] Audit Trail (정정 기록 별지)
                    # ==========================================
                    if not audit_records.empty:
                        # 별지 제목
                        can.setFont('Malgun', 14)
                        can.drawString(50, 800, "첨부. 정정 기록 보고서 (Audit Trail Report)")
                        
                        can.setFont('Malgun', 10)
                        can.drawString(50, 775, f"시험번호: {selected_test}")
                        can.line(50, 770, 545, 770) # 구분선

                        # 테이블 데이터 준비
                        # 헤더: 일시 / 일차 / 항목 / 변경 전 / 변경 후 / 사유 / 정정자
                        table_data = [['일시', '일차', '항목', '변경 전', '변경 후', '사유', '정정자', '서명']]
                        
                        for _, row in audit_records.iterrows():
                            old_val = str(row.get('변경전_값', '')).replace("['','']", "").strip("[]', ")
                            new_val = str(row.get('변경후_값', '')).replace("['','']", "").strip("[]', ")
                            
                            # 정정자 서명 이미지 처리
                            sign_cell = ""
                            sign_path = str(row.get('정정자_서명', '')).strip()
                            if sign_path:
                                img_data = download_image_from_drive(drive_service, sign_path)
                                if img_data:
                                    try:
                                        # ReportLab 테이블용 이미지 객체 (너비 40, 높이 20으로 제한)
                                        sign_cell = ImageReader(img_data)
                                    except: pass
                            
                            # 서명 이미지가 없으면 텍스트로 대체 (안전장치)
                            if not sign_cell: sign_cell = "(서명없음)"

                            table_data.append([
                                str(row['정정일시'])[:16],
                                str(row['일차']),
                                str(row.get('항목', '-')),
                                old_val,
                                new_val,
                                str(row['정정사유']),
                                str(row['정정자']),
                                sign_cell
                            ])

                        # 테이블 스타일 설정
                        # 너비 조절 (총합 약 500 정도 되게)
                        col_widths = [95, 30, 50, 80, 80, 80, 45, 45]
                        t = Table(table_data, colWidths=col_widths)
                        
                        # 스타일: 격자무늬, 헤더 배경색, 정렬 등
                        style_list = [
                            ('FONT', (0, 0), (-1, -1), 'Malgun', 8), # 글자 크기 8
                            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey), # 격자 테두리
                            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey), # 헤더 배경
                            ('ALIGN', (0, 0), (-1, -1), 'CENTER'), # 가운데 정렬
                            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), # 세로 가운데
                            # 변경 전/후, 사유는 내용이 많을 수 있으니 자동 줄바꿈을 위해 폰트 조정
                            ('FONT', (3, 1), (5, -1), 'Malgun', 7),
                        ]
                        t.setStyle(TableStyle(style_list))
                        
                        # 테이블 그리기 (위치 잡기)
                        # wrapOn으로 크기 계산 후 drawOn으로 그리기
                        w, h = t.wrapOn(can, 50, 50) 
                        # y좌표: 제목 아래(750)에서 테이블 높이만큼 뺀 위치부터 시작
                        t.drawOn(can, 50, 750 - h)

                    can.save() # 2페이지까지 저장 완료

                    # ==========================================
                    # PDF 병합 (기존 템플릿 + 새로 만든 1,2페이지)
                    # ==========================================
                    packet.seek(0)
                    new_pdf = PdfReader(packet)
                    existing_pdf = PdfReader(open("ECT-001-F01-01_어류순화기록서.pdf", "rb"))
                    output = PdfWriter()
                    
                    # 1. 원본 양식(1페이지) + 데이터(New PDF 1페이지) 병합
                    page1 = existing_pdf.pages[0]
                    page1.merge_page(new_pdf.pages[0])
                    output.add_page(page1)

                    # 2. 정정기록(New PDF 2페이지)이 있다면 그냥 추가 (별지니까)
                    if len(new_pdf.pages) > 1:
                        output.add_page(new_pdf.pages[1])

                    pdf_byte_arr = io.BytesIO()
                    output.write(pdf_byte_arr)
                    
                    st.success(f"✅ [{selected_test}] PDF 생성 완료! (Audit Trail 포함)")
                    st.download_button("📥 다운로드", pdf_byte_arr.getvalue(), f"Result_{selected_test}.pdf", "application/pdf")

                except Exception as e:
                    st.error(f"오류 발생: {e}")

    else:
        st.warning("데이터가 없습니다.")

except Exception as e:
    st.error(f"연결 오류: {e}")
