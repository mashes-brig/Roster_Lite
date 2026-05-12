import streamlit as st
import pdfplumber
import re
from datetime import datetime, timedelta
import math
import tempfile
import os
import html
from fpdf import FPDF

# --- CORE CONSTANTS & MAPPINGS ---
COLUMNS = [
    "Wk", "Total", 
    "Sun_On", "Sun_Off", "Sun_Turn", "Sun_Total",
    "Mon_On", "Mon_Off", "Mon_Turn", "Mon_Total",
    "Tue_On", "Tue_Off", "Tue_Turn", "Tue_Total",
    "Wed_On", "Wed_Off", "Wed_Turn", "Wed_Total",
    "Thu_On", "Thu_Off", "Thu_Turn", "Thu_Total",
    "Fri_On", "Fri_Off", "Fri_Turn", "Fri_Total",
    "Sat_On", "Sat_Off", "Sat_Turn", "Sat_Total"
]
DAY_MAP = {'M': 0, 'T': 1, 'W': 2, 'Th': 3, 'F': 4, 'S': 5, 'Su': 6}

# --- STATE MEMORY FIX ---
if "files_ready" not in st.session_state:
    st.session_state.files_ready = False
if "ics_data" not in st.session_state:
    st.session_state.ics_data = None
if "pdf_data" not in st.session_state:
    st.session_state.pdf_data = None
if "file_name_base" not in st.session_state:
    st.session_state.file_name_base = "Roster"

# --- HELPER FUNCTIONS ---
def get_pdf_start_date(roster_file):
    try:
        with pdfplumber.open(roster_file) as pdf:
            text = pdf.pages[0].extract_text()
            date_match = re.search(r'(\d{2}/\d{2}/\d{4})', text)
            if date_match:
                return datetime.strptime(date_match.group(1), "%d/%m/%Y")
    except:
        pass
    return datetime.now()

def get_turn_code(turn_val, prefix):
    if not turn_val: return ""
    cleaned = turn_val.replace('\n', ' ').strip()
    parts = cleaned.split()
    if "A/R" in cleaned: return "A/R"
    for i, part in enumerate(parts):
        if prefix in part:
            if i + 1 < len(parts) and parts[i+1].isdigit():
                return f"{part} {parts[i+1]}"
            return part
    return parts[0]

def decode_days_string(days_str):
    if not days_str: return set(range(7))
    days_found = []
    days_str = days_str.replace(" ", "") 
    for code in ['Th', 'Su', 'M', 'T', 'W', 'F', 'S']:
        if code in days_str:
            days_found.append(DAY_MAP[code])
            days_str = days_str.replace(code, "")
    if 'X' in days_str:
        return set(range(7)) - set(days_found)
    else:
        return set(days_found)

def clean_diagram_text(raw_text, duty_id, days_code, prefix):
    if not raw_text: return ""
    text = raw_text
    text = re.sub(r'From\s+\d{2}/\d{2}/\d{4}', '', text)
    text = re.sub(r'To\s+\d{2}/\d{2}/\d{4}', '', text)
    text = re.sub(r'On\s+\d{2}\.\d{2}', '', text)
    text = re.sub(r'Off\s+\d{2}\.\d{2}', '', text)
    text = re.sub(r'Hrs\s+\d{1,2}:\d{2}', '', text)
    text = re.sub(r'\b(?:377|387|700|313|171)\b', '', text) 
    text = re.sub(r'Days\s+[MTWThFSuOX ]+', '', text)
    text = re.sub(r'[½¼¾]', '', text) 
    text = re.sub(r'\bDVR\b', '', text) 
    text = re.sub(rf'(?:LTP|STP|VSTP)?\s*{prefix}\s*\d*\s*\d{{3,4}}', '', text, flags=re.IGNORECASE)
    text = re.sub(r'Printed on \d{2}/\d{2}/\d{4} at \d{2}:\d{2}', '', text)
    text = re.sub(r'Page:\s*\d+', '', text)
    text = re.sub(r'Routes\s+[A-Z0-9\s]+', '', text)
    text = re.sub(r'-{10,}', '', text)
    
    headers_to_remove = ["Turn No", "Loco/ Acti", "Train Working", "Train", "Cross", "Unit  vity", "Unit", "vity", "Arr", "Dep", "Id Rte", "Id", "Rte", "Refs", "Days"]
    for header in headers_to_remove:
        if "/" in header or " " in header:
            text = text.replace(header, "")
        else:
            text = re.sub(rf'\b{header}\b', '', text)
    
    cleaned_lines = [f"Duty: {duty_id}    [{days_code}]" if days_code else f"Duty: {duty_id}", "----------------------------"]
    for line in text.split('\n'):
        line = line.strip()
        if days_code: line = re.sub(rf'\b{days_code}\b\s*$', '', line)
        line = re.sub(r'\b(?:M|T|W|Th|F|S|Su|FO|SO|SuO|MO|TO|WO|ThO|SX|SuX|TWThO|MTWO|TThO|WThF)\b\s*$', '', line)
        line = re.sub(r'\s{2,}', ' ', line).strip()
        if line: cleaned_lines.append(line)
    return '\n'.join(cleaned_lines)

def build_smart_diagram_library(diagram_pdf_file, prefix):
    library = {}
    if not diagram_pdf_file: return library
    with pdfplumber.open(diagram_pdf_file) as pdf:
        for page in pdf.pages:
            raw_text = page.extract_text(layout=True)
            if not raw_text: continue
            compact_text = re.sub(r'\s+', ' ', raw_text)
            duty_match = re.search(rf'({prefix}\s*\d*\s*\d{{3,4}})', compact_text, re.IGNORECASE)
            if not duty_match: continue
            duty_id = " ".join(duty_match.group(1).split()).upper()
            days_match = re.search(r'Days ([MTWThFSuOX]+)', compact_text)
            days_code = days_match.group(1) if days_match else ""
            valid_days = decode_days_string(days_code)
            cleaned_text = clean_diagram_text(raw_text, duty_id, days_code, prefix)
            for day_index in valid_days: library[(duty_id, day_index)] = cleaned_text
    return library

def lookup_smart_diagram(diagram_library, turn_code, weekday_index):
    turn_code = " ".join(turn_code.split()).upper()
    if (turn_code, weekday_index) in diagram_library: return diagram_library[(turn_code, weekday_index)]
    number_match = re.search(r'(\d{3,4})$', turn_code)
    if number_match:
        base_num = number_match.group(1)
        for (lib_code, day_idx), text in diagram_library.items():
            if day_idx == weekday_index and lib_code.endswith(base_num): return text
        for (lib_code, day_idx), text in diagram_library.items():
            if lib_code.endswith(base_num): return text + "\n\n[Note: Exact day match wasn't found.]"
    return f"Duty: {turn_code}\n(No diagram found)"

def calculate_diagram_totals(diagram_text):
    lines = diagram_text.split('\n')
    totals = {'Drive': 0, 'Pass': 0, 'Wait': 0, 'PNB': 0, 'ECS': 0}
    current_mode = "Wait"
    last_time_dt = None
    last_time_str = ""
    pnb_start_time_str = ""
    pnb_duration = 0
    pnb_found = False
    
    for line in lines:
        clean_line = re.sub(r'[½¼¾]', '', line)
        times = re.findall(r'(?<!\d)\d{2}[\.\+:]\d{2}(?!\d)', clean_line)
        headcodes = re.findall(r'\b(\d[A-Za-z]\d{2}(?:-\d+)?)\b', clean_line)
        headcode = headcodes[0].upper() if headcodes else ""
        
        if "PNB" in clean_line:
            current_mode = "PNB"
            pnb_found = True
            continue
        if not times: continue
            
        t_first_dt = datetime.strptime(times[0].replace('.', ':').replace('+', ':'), "%H:%M")
        t_last_dt = datetime.strptime(times[-1].replace('.', ':').replace('+', ':'), "%H:%M")
        
        if last_time_dt:
            delta = t_first_dt - last_time_dt
            if delta.days < 0: delta += timedelta(days=1)
            dur = int(delta.total_seconds() / 60)
            if 0 < dur < 300: 
                totals[current_mode] = totals.get(current_mode, 0) + dur
                if current_mode == "PNB":
                    pnb_start_time_str = last_time_str.replace('.', '').replace('+', '')
                    pnb_duration = dur
        
        if len(times) > 1:
            delta2 = t_last_dt - t_first_dt
            if delta2.days < 0: delta2 += timedelta(days=1)
            dur2 = int(delta2.total_seconds() / 60)
            if dur2 > 0: totals['Wait'] += dur2
                
        if "PASS" in clean_line: current_mode = "Pass"
        elif headcode:
            if headcode.startswith('5'): current_mode = "ECS"
            else: current_mode = "Drive"
        elif "SHUNT" in clean_line.upper() or "DEPOT" in clean_line.upper() or "ECS" in clean_line.upper(): current_mode = "ECS"
        else: current_mode = "Wait"
            
        last_time_dt = t_last_dt
        last_time_str = times[-1]
        
    pnb_title_str = f" (PNB {pnb_start_time_str} {pnb_duration})" if pnb_found and pnb_duration > 0 else ""
    summary_text = f"Drive: {totals['Drive']}m | Pass: {totals['Pass']}m | Wait/Walking: {totals['Wait']}m | PNB: {totals['PNB']}m | ECS & Shunt: {totals['ECS']}m"
    return pnb_title_str, summary_text

def create_fridge_pdf(weeks_data, prefix):
    pdf = FPDF(orientation='P', unit='mm', format='A4')
    pdf.set_auto_page_break(auto=False, margin=0) 
    pdf.add_page()
    
    pdf.set_font("Helvetica", 'B', 16)
    pdf.cell(0, 8, txt=f"My {prefix} Roster", ln=True, align='C')
    pdf.ln(2)

    days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
    left_margin = 10
    usable_width = 210 - (left_margin * 2)
    wc_w = 15 
    col_w = (usable_width - wc_w) / 7 
    
    num_weeks = len(weeks_data) if len(weeks_data) > 0 else 1
    page1_avail = 256 
    page2_avail = 266 
    
    row_h = 18.0
    for h in range(180, 48, -1):
        test_h = h / 10.0
        p1_rows = int(page1_avail // test_h)
        p2_rows = int(page2_avail // test_h)
        if p1_rows >= num_weeks or (p1_rows + p2_rows) >= num_weeks:
            row_h = test_h
            break
            
    main_font_size = max(4.0, min(7.5, row_h * 0.55))
    
    pdf.set_font("Helvetica", 'B', 8)
    pdf.set_x(left_margin)
    pdf.cell(wc_w, 6, "W/C", border=1, align='C')
    for day in days:
        pdf.cell(col_w, 6, day, border=1, align='C')
    pdf.ln()

    for week in weeks_data:
        if pdf.get_y() + row_h > 282: 
            pdf.add_page()
            pdf.set_font("Helvetica", 'B', 8)
            pdf.set_x(left_margin)
            pdf.cell(wc_w, 6, "W/C", border=1, align='C')
            for day in days:
                pdf.cell(col_w, 6, day, border=1, align='C')
            pdf.ln()

        y = pdf.get_y()
        pdf.set_x(left_margin)
        wc_date = week.get('wc_date', '')[:5] 
        wk_num = week.get('wk_num', '')
        
        pdf.set_fill_color(255, 255, 255)
        pdf.set_xy(left_margin, y)
        pdf.cell(wc_w, row_h, "", border=1, fill=True)
        
        line_spacing = main_font_size / 2.5
        pdf.set_font("Helvetica", 'B', main_font_size)
        pdf.set_xy(left_margin, y + (row_h/2) - line_spacing)
        pdf.cell(wc_w, line_spacing, wc_date, align='C')
        pdf.set_font("Helvetica", '', main_font_size * 0.8)
        pdf.set_xy(left_margin, y + (row_h/2))
        pdf.cell(wc_w, line_spacing, f"Line {wk_num}", align='C')

        for i, day in enumerate(days):
            x = left_margin + wc_w + (i * col_w)
            shift = week.get(day, {})
            duty = shift.get('duty', '')

            if "RD" in duty: 
                pdf.set_fill_color(225, 225, 225) 
            elif shift.get('on'):
                try:
                    on_hr = int(shift['on'].split(':')[0])
                    if on_hr < 11:
                        pdf.set_fill_color(210, 240, 255)
                    else:
                        pdf.set_fill_color(255, 230, 200)
                except: 
                    pdf.set_fill_color(255, 255, 255)
            else: 
                pdf.set_fill_color(255, 255, 255)

            pdf.set_xy(x, y)
            pdf.cell(col_w, row_h, "", border=1, fill=True)
            
            if duty:
                if "RD" in duty:
                    pdf.set_xy(x, y + (row_h/2) - (main_font_size/4))
                    pdf.set_font("Helvetica", 'B', main_font_size)
                    pdf.cell(col_w, main_font_size/2, duty, align='C')
                elif shift.get('on'):
                    start_y = y + (row_h - (line_spacing * 3)) / 2
                    pdf.set_font("Helvetica", 'B', main_font_size)
                    pdf.set_xy(x, start_y); pdf.cell(col_w, line_spacing, duty, align='C')
                    pdf.set_font("Helvetica", '', main_font_size)
                    pdf.set_xy(x, start_y + line_spacing); pdf.cell(col_w, line_spacing, f"{shift['on']}-{shift['off']}", align='C')
                    pdf.set_xy(x, start_y + (line_spacing * 2)); pdf.cell(col_w, line_spacing, f"({shift['total']})", align='C')

        pdf.set_xy(left_margin, y + row_h)

    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
        pdf.output(tmp.name)
        with open(tmp.name, 'rb') as f:
            pdf_bytes = f.read()
    os.remove(tmp.name)
    return pdf_bytes

def small_code(text):
    safe_text = html.escape(text)
    st.markdown(f"<div style='font-family: monospace; font-size: 11px; line-height: 1.4; padding: 10px; border-radius: 5px; background-color: rgba(128, 128, 128, 0.1); color: inherit; overflow-x: auto; white-space: pre-wrap;'>{safe_text}</div>", unsafe_allow_html=True)

# --- WEB APP UI ---
st.set_page_config(page_title="Roster Lite", page_icon="🚆")

# HEADER & INSTRUCTIONS
st.title("🚆 Roster Lite: iCal Creator")

st.markdown("""
Welcome to **Roster Lite**! This web app takes your PDF base rosters and duty diagrams and instantly converts them into a smartphone-ready calendar (.ics) and a printable Fridge Roster (.pdf).
""")

st.warning("""
**⚠️ IMPORTANT DISCLAIMERS:**
* **Base Roster Only:** This app only generates your shifts based on the *long-term base roster*. It will **not** automatically update with changes from the short-term Daily or Weekly rosters. You must still check those manually when published.
* **Check Accuracy:** Always verify the generated calendar against your official roster before relying on it for your duties.
* **iPhone Calendar:** When you open the downloaded `.ics` file on an iPhone, it will add the shifts to your *Default Calendar*. To change this, go to your iPhone **Settings > Calendar > Default Calendar** before opening the file.
""")

with st.expander("📖 Open User Guide & Instructions"):
    st.markdown("""
    ### Step-by-Step Instructions
    
    **Step 1: Upload Documents**
    * **Base Roster PDF:** Upload the grid showing your link's base roster (it can be 1 or 2 pages depending on the number of lines).
    * **Master Diagram (Large PDF):** If you want detailed notes (like train working times), upload the Master Diagram document here. *(Note: This must be a single file. If you have multiple diagram PDFs, please combine them first. This upload box only appears if you select calendar features that require it).*

    **Step 2: Base Roster Settings**
    * **Start Week No (Your Line):** Enter the line number you are currently working.
    * **Duty Prefix / Link:** Enter your link identifier (e.g., **BID**).
    * **Date you are on this Line:** This is your anchor point. Enter the exact **Sunday** you are working the line number you entered above. As soon as you upload your Base Roster, the app will automatically find the official start date and put it in this box for you. You only need to manually change this date if you are moving links or starting mid-timetable.
    * **Timetable End Date:** Set the date the current timetable expires.

    **Step 3: Calendar Customization**
    *Customize exactly what information gets saved into your calendar events using the checkboxes. Uncheck everything for a purely minimal calendar.*
    * **Duty Diagram Notes:** Copies your exact step-by-step train working and break times into the event description.
    * **Time Summary:** Calculates and prints your total driving, passing, and PNB times at the bottom of the event notes.
    * **PNB in Event Title:** Adds your PNB start time and duration directly to the event title so you can view it from your lock screen.

    **Step 4: Generate & Download**
    Once you are happy with your settings, click the blue **Generate Files** button.
    * **📅 Download Calendar (.ics):** Tap this to add the shifts to your phone's calendar app.
    * **🖨️ Download Fridge Roster (.pdf):** Tap this to view or print your color-coded weekly shift grid.
    """)

st.divider()

# --- 1. CORE ROSTER DETAILS ---
st.header("1. Base Roster Settings")
st.write("Upload your base roster grid and set your dates.")
roster_file = st.file_uploader("Upload Base Roster PDF", type=["pdf"])

# Extract default date if file uploaded
default_start = get_pdf_start_date(roster_file) if roster_file else datetime.now()

col1, col2 = st.columns(2)
with col1:
    week_no = st.number_input("Start Week No (Your Line):", min_value=1, value=1)
    prefix = st.text_input("Duty Prefix / Link:", value="BID").strip().upper()
with col2:
    user_start_date = st.date_input("Date you are on this Line (Must be a Sunday):", value=default_start)
    end_date_input = st.date_input("Timetable End Date:", value=datetime(2026, 12, 12))

st.caption("*ℹ️ **How this works:** Enter your Line number and the exact Sunday you are working that line. By default, this jumps to the official start date of the Base Roster when uploaded.*")

st.markdown("#### Default Output")
st.caption("*By default, if you uncheck all customization options below, your calendar event title will look purely minimal like this:*")
small_code("15:15 23:58 BID3 157 (08:43)")

st.divider()

# --- 2. CALENDAR CUSTOMIZATION ---
st.header("2. Calendar Customization")
st.markdown("#### 📝 Duty Diagram Features")
c1, c2 = st.columns([1.5, 1])
with c1:
    opt_diagram = st.checkbox("Include full Duty Diagram in calendar notes", value=True)
    st.caption("*Pastes step-by-step train working into notes.*")
with c2:
    small_code("""Duty: BID3 157    [FO]
----------------------------
PASS Brighton 15.28 1S42
Barnham 16.17
RW Barnham 16.29 16.31 1C40 OBS
Pmth&Ssea 17.19 17.33 1C55 OBS
AP Horsham 18.47
PASS Horsham 18.51 1B55 SAME
T Bds 19.04
RW T Bds 19.13 19.14 1C54 OBS
RAPD Horsham 19.27
PNB
RWPA Horsham 20.41 20.50 1B63 QL, Sel OBS
TC Vic (C) 21.45
PC Vic (C) 22.59 1W52 QL, Sel OBS
Gat Ap 23.29 23.31 1W52
TC Brighton 23.57""")

c1, c2 = st.columns([1.5, 1])
with c1:
    opt_math = st.checkbox("Include Time Summary in calendar notes", value=False)
    st.caption("*Calculates driving/break totals.*")
with c2:
    small_code("Drive: 246m | Pass: 62m | Wait: 127m | PNB: 74m")

c1, c2 = st.columns([1.5, 1])
with c1:
    opt_pnb_title = st.checkbox("Include PNB time & length in Event Title", value=False)
    st.caption("*See breaks on your lock screen.*")
with c2:
    small_code("15:15 23:58 BID3 157 (08:43) (PNB 1927 74)")

needs_diagram = opt_diagram or opt_math or opt_pnb_title
if needs_diagram:
    st.info("💡 Please upload your Master Diagram PDF. *(Note: If your diagrams are across multiple files, please combine them into one PDF first)*:")
    diagram_file = st.file_uploader("Upload Master Diagram (Large PDF)", type=["pdf"])
else:
    diagram_file = None

st.divider()
st.markdown("#### 📅 Extra Features")
c1, c2 = st.columns([1.5, 1])
with c1:
    opt_weekly_sum = st.checkbox("Generate a '📋 Weekly Summary' event on Sundays", value=True)
    st.caption("*View your whole week in one event.*")
with c2:
    small_code("Sun: RD Sunday\nMon: RD\nTue: RD\nWed: 14:00 23:23 A/R (09:23)...")

opt_pdf = st.checkbox("Generate a printable 'Fridge Roster' PDF", value=True)

# --- 3. SHIFT ALERTS ---
st.header("3. Shift Alerts")
opt_alarms = st.checkbox("Set automated shift wake-up alarms", value=False)
if opt_alarms:
    col_a, col_b = st.columns(2)
    with col_a: early_alarm = st.number_input("Early Shifts (< 11:30) Alert Mins:", min_value=0, value=90)
    with col_b: late_alarm = st.number_input("Late Shifts (>= 11:30) Alert Mins:", min_value=0, value=105)

st.divider()

if st.button("Generate Files", type="primary"):
    if not roster_file:
        st.error("⚠️ Please upload the Base Roster PDF.")
    elif needs_diagram and not diagram_file:
        st.error("⚠️ Please upload the Master Diagram.")
    elif user_start_date.weekday() != 6:
        st.error("⚠️ Please change 'Date you are on this Line' to a Sunday. The calendar engine requires weeks to start on Sundays.")
    else:
        with st.spinner("Calculating Shifts..."):
            try:
                diagram_library = build_smart_diagram_library(diagram_file, prefix) if needs_diagram else {}
                
                with pdfplumber.open(roster_file) as pdf:
                    all_data = []
                    for page in pdf.pages:
                        for table in page.extract_tables():
                            for row in table[2:]:
                                if row and row[0] is not None and len(row) == len(COLUMNS): all_data.append(row)

                roster_data = {int(r[0]): dict(zip(COLUMNS, r)) for r in all_data if str(r[0]).isdigit()}
                
                start_date = datetime.combine(user_start_date, datetime.min.time())
                target_date = datetime.combine(end_date_input, datetime.min.time())
                total_weeks = len(roster_data)
                total_days = (target_date - start_date).days + 1
                total_weeks_needed = math.ceil(total_days / 7)

                st.session_state.file_name_base = f"{prefix}_Line_{week_no}_{start_date.strftime('%d-%m-%Y')}"

                ics_lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Smart Roster Lite//EN", "CALSCALE:GREGORIAN"]
                days_list = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
                pdf_roster_data_grid = []

                for i in range(total_weeks_needed):
                    wk_num = ((week_no - 1 + i) % total_weeks) + 1
                    current_week_start = start_date + timedelta(days=i * 7)
                    row = roster_data.get(wk_num)
                    if not row: continue
                    
                    week_dict = {'wc_date': current_week_start.strftime('%d/%m/%y'), 'wk_num': wk_num}
                    current_week_summary = []

                    for day_idx, day in enumerate(days_list):
                        current_date = current_week_start + timedelta(days=day_idx)
                        if current_date > target_date: break
                            
                        on_val = str(row[f"{day}_On"]).strip()
                        off_val = str(row[f"{day}_Off"]).strip()
                        turn_val = str(row[f"{day}_Turn"]).strip().replace('\n', ' ')
                        total_val = str(row[f"{day}_Total"]).strip()

                        if "RD" in on_val or "RD" in turn_val:
                            summary = "RD Sunday" if day == 'Sun' else "RD"
                            ics_lines.extend(["BEGIN:VEVENT", f"DTSTART;VALUE=DATE:{current_date.strftime('%Y%m%d')}", f"SUMMARY:{summary}", "END:VEVENT"])
                            current_week_summary.append(f"{day}: {summary}")
                            week_dict[day] = {'duty': summary, 'on': '', 'off': '', 'total': ''}
                            continue

                        if not on_val or on_val == 'None': continue

                        turn_code = get_turn_code(turn_val, prefix)
                        raw_diagram = lookup_smart_diagram(diagram_library, turn_code, current_date.weekday()) if needs_diagram else ""
                        pnb_title_str, math_summary_str = calculate_diagram_totals(raw_diagram) if needs_diagram else ("", "")

                        on_time_clean = re.sub(r'[^0-9\.\+:]', '', on_val.split()[0]).replace('.',':').replace('+',':').replace('::',':')
                        off_time_clean = re.sub(r'[^0-9\.\+:]', '', off_val.split()[0]).replace('.',':').replace('+',':').replace('::',':')
                        if len(on_time_clean) == 4 and ":" not in on_time_clean: on_time_clean = f"{on_time_clean[:2]}:{on_time_clean[2:]}"
                        if len(off_time_clean) == 4 and ":" not in off_time_clean: off_time_clean = f"{off_time_clean[:2]}:{off_time_clean[2:]}"
                        
                        week_dict[day] = {'duty': turn_code, 'on': on_time_clean, 'off': off_time_clean, 'total': total_val}
                        title = f"{on_time_clean} {off_time_clean} {turn_code} ({total_val}){pnb_title_str if opt_pnb_title else ''}"
                        current_week_summary.append(f"{day}: {title}")

                        desc_parts = []
                        if opt_diagram and raw_diagram: desc_parts.append(raw_diagram.replace('\n', '\\n').replace(',', '\\,'))
                        if opt_math and math_summary_str: desc_parts.append(math_summary_str)
                        desc_final = "\\n----------------------------\\n".join(desc_parts)

                        try:
                            on_dt = datetime.strptime(f"{current_date.strftime('%Y%m%d')} {on_time_clean}", "%Y%m%d %H:%M")
                            off_dt = datetime.combine(current_date + (timedelta(days=1) if off_time_clean < on_time_clean else timedelta(0)), datetime.strptime(off_time_clean, "%H:%M").time())
                            ics_lines.extend(["BEGIN:VEVENT", f"DTSTART:{on_dt.strftime('%Y%m%dT%H%M%S')}", f"DTEND:{off_dt.strftime('%Y%m%dT%H%M%S')}", f"SUMMARY:{title}"])
                            if desc_final: ics_lines.append(f"DESCRIPTION:{desc_final}")
                            if opt_alarms:
                                alarm_mins = early_alarm if on_dt.time() < datetime.strptime("11:30", "%H:%M").time() else late_alarm
                                ics_lines.extend(["BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:Reminder", f"TRIGGER:-PT{alarm_mins}M", "END:VALARM"])
                            ics_lines.append("END:VEVENT")
                        except: continue
                    
                    if len(week_dict) > 2: pdf_roster_data_grid.append(week_dict)
                    if opt_weekly_sum and current_week_summary:
                        sum_text = "\\n".join(current_week_summary)
                        ics_lines.extend(["BEGIN:VEVENT", f"DTSTART;VALUE=DATE:{current_week_start.strftime('%Y%m%d')}", f"SUMMARY:📋 Weekly Summary - Line {wk_num}", f"DESCRIPTION:{sum_text}", "END:VEVENT"])

                ics_lines.append("END:VCALENDAR")
                st.session_state.ics_data = "\n".join(ics_lines)
                st.session_state.pdf_data = create_fridge_pdf(pdf_roster_data_grid, prefix) if opt_pdf else None
                st.session_state.files_ready = True
            except Exception as e: st.error(f"Error: {e}")

if st.session_state.files_ready:
    st.success("✅ Files Ready!")
    c_dl1, c_dl2 = st.columns(2)
    with c_dl1: st.download_button("📅 Calendar (.ics)", st.session_state.ics_data, f"{st.session_state.file_name_base}.ics", "text/calendar", use_container_width=True)
    if st.session_state.pdf_data:
        with c_dl2: st.download_button("🖨️ Fridge Roster (.pdf)", st.session_state.pdf_data, f"{st.session_state.file_name_base}.pdf", "application/pdf", use_container_width=True)
