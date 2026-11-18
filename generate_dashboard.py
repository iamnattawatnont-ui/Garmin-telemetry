import os
import io
import base64
import tarfile
import json
import hashlib
from datetime import datetime, timedelta
from garminconnect import Garmin
from google import genai

def init_garmin():
    token_b64 = os.environ.get("GARMIN_TOKEN_B64", "").strip()
    token_dir = os.path.expanduser("~/.garminconnect")
    if token_b64 and not os.path.exists(token_dir):
        tar_bytes = base64.b64decode(token_b64)
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
            tar.extractall(path=os.path.expanduser("~"))
    client = Garmin(
        os.environ.get("GARMIN_EMAIL", "").strip(),
        os.environ.get("GARMIN_PASSWORD", "").strip()
    )
    client.login(tokenstore=token_dir)
    return client

def gather_data(client):
    tz_now = datetime.utcnow() + timedelta(hours=7)
    today_str = tz_now.date().isoformat()
    yesterday_str = (tz_now.date() - timedelta(days=1)).isoformat()

    summary_today = client.get_user_summary(today_str)
    summary_yesterday = client.get_user_summary(yesterday_str)
    sleep_data = client.get_sleep_data(today_str)
    sleep_dto = sleep_data.get("dailySleepDTO", {})

    activities = client.get_activities(0, 14)
    seven_days_ago = tz_now.date() - timedelta(days=7)

    workouts = []
    cardio_min_7d = 0.0
    strength_count_7d = 0
    daily_cardio_map = {(tz_now.date() - timedelta(days=i)).isoformat(): 0.0 for i in range(6, -1, -1)}

    for act in activities:
        act_date = act.get("startTimeLocal", "")[:10]
        if act_date >= seven_days_ago.isoformat():
            dur_min = round(act.get("duration", 0) / 60, 1)
            act_type = act.get("activityType", {}).get("typeKey", "")

            if any(k in act_type for k in ["walk", "run", "treadmill", "cycling", "cardio"]):
                cardio_min_7d += dur_min
                if act_date in daily_cardio_map:
                    daily_cardio_map[act_date] += dur_min
            elif any(k in act_type for k in ["strength", "training", "weight"]):
                strength_count_7d += 1

            workouts.append({
                "date": act_date,
                "name": act.get("activityName"),
                "duration_min": dur_min,
                "avg_hr": act.get("averageHR") or "-",
                "calories": round(act.get("calories", 0))
            })

    return {
        "updated_at": tz_now.strftime("%d %b %Y • %H:%M น."),
        "body_battery": summary_today.get("bodyBatteryMostRecentValue") or summary_yesterday.get("bodyBatteryMostRecentValue") or 0,
        "resting_hr": summary_today.get("restingHeartRate") or summary_yesterday.get("restingHeartRate") or "-",
        "steps_yesterday": summary_yesterday.get("totalSteps") or 0,
        "sleep_score": sleep_dto.get("sleepScores", {}).get("overall", {}).get("value") or 0,
        "sleep_hours": round(sleep_dto.get("sleepTimeSeconds", 0) / 3600, 1),
        "deep_sleep_min": round(sleep_dto.get("deepSleepSeconds", 0) / 60),
        "cardio_min_7d": round(cardio_min_7d, 1),
        "strength_count_7d": strength_count_7d,
        "chart_labels": list(daily_cardio_map.keys()),
        "chart_values": [round(v, 1) for v in daily_cardio_map.values()],
        "workouts": workouts[:6]
    }

def get_gemini_insight(data):
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return "ยังไม่ได้ตั้งค่า GEMINI_API_KEY"
    client = genai.Client(api_key=api_key)
    prompt = f"""
    คุณคือโค้ชฟิตเนสส่วนตัว วิเคราะห์ข้อมูล Garmin Connect ล่าสุดนี้ (เป้าหมาย: Body Recomposition เล่นเวท Dumbbell และเดินชัน Zone 2 สัปดาห์ละ 150-180 นาที ทาน Creatine 5g/วัน):
    {json.dumps(data, ensure_ascii=False)}

    สรุปคำแนะนำเป็นภาษาไทย 3 ข้อสั้นๆ กระชับ ตรงประเด็น (ใช้แท็ก HTML <b> และ <br> จัดหน้าให้อ่านง่าย ไม่ต้องใส่ Markdown ```):
    1. สถานะความพร้อมของร่างกายวันนี้
    2. ความคืบหน้าเป้า Zone 2 (150-180 นาที/สัปดาห์) และการเล่นเวท
    3. แผนแนะนำสำหรับวันนี้
    """
    res = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
    return res.text

def build_html(data, insight_html):
    pin = os.environ.get("DASHBOARD_PIN", "1234").strip()
    pin_hash = hashlib.sha256(pin.encode()).hexdigest()

    workout_rows = "".join([
        f"""
        <div class="flex justify-between items-center py-3 border-b border-slate-800/80 last:border-0">
          <div>
            <p class="font-medium text-slate-100 text-sm">{w['name']}</p>
            <p class="text-xs text-slate-400">{w['date']} • Avg HR {w['avg_hr']} bpm</p>
          </div>
          <div class="text-right">
            <span class="text-emerald-400 font-semibold text-sm">{w['duration_min']} นาที</span>
            <p class="text-xs text-slate-500">{w['calories']} kcal</p>
          </div>
        </div>
        """ for w in data["workouts"]
    ]) or "<p class='text-slate-500 text-sm'>ยังไม่มีบันทึกการออกกำลังกายใน 7 วันล่าสุด</p>"

    progress_pct = min(100, round((data["cardio_min_7d"] / 180) * 100))

    html = f"""<!DOCTYPE html>
<html lang="th" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0" />
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
  <title>Garmin Telemetry</title>
  <script src="[https://cdn.tailwindcss.com](https://cdn.tailwindcss.com)"></script>
  <script src="[https://cdn.jsdelivr.net/npm/chart.js](https://cdn.jsdelivr.net/npm/chart.js)"></script>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen font-sans selection:bg-emerald-500 selection:text-white">

  <div id="lockScreen" class="fixed inset-0 bg-slate-950 z-50 flex flex-col items-center justify-center p-6">
    <div class="bg-slate-900 border border-slate-800 p-6 rounded-2xl w-full max-w-xs text-center space-y-4">
      <h2 class="text-lg font-bold text-emerald-400">GARMIN TELEMETRY</h2>
      <p class="text-xs text-slate-400">กรอกรหัส PIN เพื่อเข้าดูข้อมูลสุขภาพส่วนตัว</p>
      <input type="password" id="pinInput" inputmode="numeric" placeholder="••••" class="w-full text-center tracking-widest text-xl bg-slate-950 border border-slate-700 rounded-xl py-2.5 focus:outline-none focus:border-emerald-500" />
      <button onclick="unlock()" class="w-full bg-emerald-600 hover:bg-emerald-500 text-white font-medium py-2.5 rounded-xl transition">ปลดล็อก</button>
      <p id="errMsg" class="text-xs text-rose-400 hidden">รหัส PIN ไม่ถูกต้อง</p>
    </div>
  </div>

  <main id="dashboardContent" class="hidden max-w-3xl mx-auto p-4 pb-12 space-y-5">
    <header class="flex justify-between items-center pt-2">
      <div>
        <h1 class="text-xl font-bold tracking-tight text-white">Garmin Telemetry</h1>
        <p class="text-xs text-slate-400">อัปเดตล่าสุด: {data['updated_at']}</p>
      </div>
      <span class="px-2.5 py-1 text-[11px] font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 rounded-full">Auto-Sync</span>
    </header>

    <section class="grid grid-cols-2 sm:grid-cols-4 gap-3">
      <div class="bg-slate-900/90 border border-slate-800 p-4 rounded-2xl">
        <p class="text-[11px] text-slate-400 uppercase font-semibold">Body Battery</p>
        <p class="text-3xl font-bold text-cyan-400 mt-1">{data['body_battery']}</p>
        <p class="text-[11px] text-slate-500 mt-1">เต็ม 100</p>
      </div>
      <div class="bg-slate-900/90 border border-slate-800 p-4 rounded-2xl">
        <p class="text-[11px] text-slate-400 uppercase font-semibold">Sleep Score</p>
        <p class="text-3xl font-bold text-indigo-400 mt-1">{data['sleep_score']}</p>
        <p class="text-[11px] text-slate-500 mt-1">{data['sleep_hours']} ชม. (Deep {data['deep_sleep_min']}น.)</p>
      </div>
      <div class="bg-slate-900/90 border border-slate-800 p-4 rounded-2xl">
        <p class="text-[11px] text-slate-400 uppercase font-semibold">Resting HR</p>
        <p class="text-3xl font-bold text-rose-400 mt-1">{data['resting_hr']}</p>
        <p class="text-[11px] text-slate-500 mt-1">bpm</p>
      </div>
      <div class="bg-slate-900/90 border border-slate-800 p-4 rounded-2xl">
        <p class="text-[11px] text-slate-400 uppercase font-semibold">เวท (7 วัน)</p>
        <p class="text-3xl font-bold text-amber-400 mt-1">{data['strength_count_7d']}</p>
        <p class="text-[11px] text-slate-500 mt-1">เซสชัน</p>
      </div>
    </section>

    <section class="bg-slate-900/90 border border-slate-800 p-5 rounded-2xl space-y-3">
      <div class="flex justify-between items-end">
        <div>
          <p class="text-xs text-slate-400 uppercase font-semibold">Zone 2 Cardio (7 วันล่าสุด)</p>
          <p class="text-2xl font-bold text-emerald-400 mt-0.5">{data['cardio_min_7d']} <span class="text-sm font-normal text-slate-400">/ 150–180 นาที</span></p>
        </div>
        <span class="text-xs font-semibold text-emerald-400">{progress_pct}%</span>
      </div>
      <div class="w-full bg-slate-800 h-2.5 rounded-full overflow-hidden">
        <div class="bg-emerald-500 h-full rounded-full" style="width: {progress_pct}%"></div>
      </div>
      <div class="pt-2">
        <canvas id="cardioChart" height="110"></canvas>
      </div>
    </section>

    <section class="bg-gradient-to-br from-slate-900 to-slate-900/60 border border-emerald-500/30 p-5 rounded-2xl space-y-2">
      <div class="flex items-center gap-2">
        <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
        <h2 class="text-xs font-bold uppercase tracking-wider text-emerald-400">Gemini Coach Insight</h2>
      </div>
      <div class="text-sm text-slate-200 leading-relaxed pt-1">
        {insight_html}
      </div>
    </section>

    <section class="bg-slate-900/90 border border-slate-800 p-5 rounded-2xl">
      <h2 class="text-xs font-bold uppercase tracking-wider text-slate-400 mb-2">Recent Workouts</h2>
      <div>{workout_rows}</div>
    </section>
  </main>

  <script>
    const TARGET_HASH = "{pin_hash}";

    async function sha256(str) {{
      const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(str));
      return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, "0")).join("");
    }}

    async function unlock() {{
      const val = document.getElementById("pinInput").value;
      const hash = await sha256(val);
      if (hash === TARGET_HASH) {{
        localStorage.setItem("garmin_dash_pin", val);
        showDashboard();
      }} else {{
        document.getElementById("errMsg").classList.remove("hidden");
      }}
    }}

    function showDashboard() {{
      document.getElementById("lockScreen").classList.add("hidden");
      document.getElementById("dashboardContent").classList.remove("hidden");
      renderChart();
    }}

    (async () => {{
      const saved = localStorage.getItem("garmin_dash_pin");
      if (saved && (await sha256(saved)) === TARGET_HASH) {{
        showDashboard();
      }}
    }})();

    function renderChart() {{
      const ctx = document.getElementById('cardioChart').getContext('2d');
      new Chart(ctx, {{
        type: 'bar',
        data: {{
          labels: {json.dumps(data['chart_labels'])},
          datasets: [{{
            label: 'นาทีคาร์ดิโอ',
            data: {json.dumps(data['chart_values'])},
            backgroundColor: '#10b981',
            borderRadius: 6
          }}]
        }},
        options: {{
          plugins: {{ legend: {{ display: false }} }},
          scales: {{
            x: {{ grid: {{ display: false }}, ticks: {{ color: '#94a3b8', font: {{ size: 10 }} }} }},
            y: {{ grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8', font: {{ size: 10 }} }} }}
          }}
        }}
      }});
    }}
  </script>
</body>
</html>"""

    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)

if __name__ == "__main__":
    client = init_garmin()
    data = gather_data(client)
    insight = get_gemini_insight(data)
    build_html(data, insight)
    print("สร้างหน้าเว็บ docs/index.html สำเร็จ!")
