from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
import requests
import re

app = Flask(__name__)
CORS(app)

# 🚨 새롭게 추가할 5줄 (내 IP 스파이)
try:
    my_ip = requests.get("https://api.ipify.org", timeout=3).text
    print(f"\n\n🚀🚀🚀 내 클라우드 서버 IP: {my_ip} 🚀🚀🚀\n\n", flush=True)
except:
    pass

# 🔑 완벽하게 인증 통과된 제민님의 새 API 키
ODSAY_API_KEY = "xTses587ntx0ITz4NXkSPbtckAnLk+y5ikHjr+FdoQU"

DISTANCES = {
    "bima": {"11285": 150, "11279": 500},     
    "chambit": {"11285": 500, "11279": 850},  
    "library": {"11285": 250, "11279": 600}   
}
LOC_NAMES = {"bima": "비마관", "chambit": "참빛관", "library": "중앙도서관"}
STATION_NAMES = {"11285": "정문 앞", "11279": "광운대역"}

STATION_ID_CACHE = {}

def parse_arrmsg(time_data):
    # 1. 오디세이가 이상한 포장지(딕셔너리)로 줬을 경우 껍질을 벗깁니다.
    if isinstance(time_data, dict):
        if 'arrmsg1' in time_data: time_data = time_data['arrmsg1']
        elif '#text' in time_data: time_data = time_data['#text']
        else: time_data = str(time_data)
        
    time_str = str(time_data).strip()
    
    # 2. 정보가 없으면 거릅니다.
    if not time_str or time_str in ["{}", "None", "0", "-1"]: return 9999, "정보 없음"
    if "곧 도착" in time_str or "운행중" in time_str: return 60, "곧 도착"
    if "운행종료" in time_str or "출발대기" in time_str: return 9999, "종료/대기"
    
    # 3. 정상적으로 '분', '초' 글씨가 있는지 확인
    minutes, seconds = 0, 0
    m_match = re.search(r'(\d+)분', time_str)
    s_match = re.search(r'(\d+)초', time_str)
    
    if m_match or s_match:
        if m_match: minutes = int(m_match.group(1))
        if s_match: seconds = int(s_match.group(1))
        
        station_match = re.search(r'(\d+)번째', time_str)
        stations_left = f"{station_match.group(1)}번째 전" if station_match else ""
        
        return (minutes * 60) + seconds, stations_left
        
    # 4. '분', '초' 글씨 없이 숫자만 덩그러니 온 경우 (초 단위로 계산해버림)
    if time_str.isdigit():
        return int(time_str), "초 (추정)"
        
    # 5. 그래도 도저히 해석할 수 없다면? 화면에 원본 데이터를 까발립니다! (디버깅용)
    return 9999, f"[오류확인] {time_str[:20]}"

def calculate_action(bus_seconds, distance):
    walk_speed = 1.27; run_speed = 4.0; buffer_time = 30  
    walk_time = (distance / walk_speed) + buffer_time
    run_time = (distance / run_speed) + buffer_time
    
    if bus_seconds >= walk_time: return "walk", "여유롭게 걸어가도 탈 수 있어요!", "걷기", 1
    elif bus_seconds >= run_time: return "run", "지금 뛰면 탈 수 있어요!", "뛰기", 2
    else: return "giveup", "뛰어도 늦어요. 다음 버스를 노리세요!", "포기", 3

def handle_odsay_error(res):
    if "error" in res:
        err = res["error"]
        if isinstance(err, list) and len(err) > 0: return err[0].get("message", "알 수 없는 에러")
        elif isinstance(err, dict): return err.get("message", err.get("msg", "알 수 없는 에러"))
        return str(err)
    return None

def get_odsay_station_id(ars_id):
    if ars_id in STATION_ID_CACHE: return STATION_ID_CACHE[ars_id]
    
    search_names = ["광운대", "광운대학교", "광운대역"]
    
    for name in search_names:
        url = "https://api.odsay.com/v1/api/searchStation"
        params = {"lang": "0", "stationName": name, "stationClass": "1", "count": "100", "apiKey": ODSAY_API_KEY}
        
        res = requests.get(url, params=params, timeout=5).json()
        err_msg = handle_odsay_error(res)
        if err_msg: raise Exception(f"ODsay 검색 에러: {err_msg}")
        
        if "result" in res and "station" in res["result"]:
            for st in res["result"]["station"]:
                st_ars = str(st.get("arsID", "")).replace("-", "")
                if st_ars == ars_id:
                    STATION_ID_CACHE[ars_id] = st.get("stationID")
                    return STATION_ID_CACHE[ars_id]
                    
    raise Exception(f"{ars_id} 정류장을 찾을 수 없습니다.")

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/bus')
def get_bus_data():
    start_loc = request.args.get('start', 'bima')
    if start_loc not in DISTANCES: start_loc = 'bima'
        
    start_loc_name = LOC_NAMES[start_loc]
    target_stations = ["11285", "11279"]
    all_buses = []
    
    try:
        for arsId in target_stations:
            odsay_station_id = get_odsay_station_id(arsId)
            distance = DISTANCES[start_loc][arsId]
            
            realtime_url = "https://api.odsay.com/v1/api/realtimeStation"
            params = {"lang": "0", "stationID": odsay_station_id, "apiKey": ODSAY_API_KEY}
            
            res = requests.get(realtime_url, params=params, timeout=5).json()
            err_msg = handle_odsay_error(res)
            if err_msg: raise Exception(f"실시간 데이터 에러: {err_msg}")
            
            if "result" in res and "real" in res["result"]:
                for bus in res["result"]["real"]:
                    rtNm = str(bus.get("routeNm") or bus.get("routeName"))
                    if any(b in rtNm for b in ['261', '1144', '1137', '163']):
                        arrmsg1 = bus.get("arrmsg1") or bus.get("arrival1")
                        if not arrmsg1: continue
                        
                        bus_seconds, stations_left = parse_arrmsg(arrmsg1)
                        if bus_seconds < 9000:
                            status_type, msg, action_txt, priority = calculate_action(bus_seconds, distance)
                            direction = "석계역 방면" if arsId == "11285" else "광운대역 방면"
                            mins, secs = divmod(bus_seconds, 60)
                            
                            all_buses.append({
                                "bus_number": rtNm,
                                "direction": direction,
                                "station_name": STATION_NAMES[arsId],
                                "distance_str": f"{distance}m", 
                                "seconds": bus_seconds,
                                "time_str": f"{mins}분 {secs}초" if mins > 0 else f"{secs}초",
                                "stations_left": stations_left,
                                "status_type": status_type,
                                "message": msg,
                                "action_txt": action_txt,
                                "priority": priority,
                                "path_str": f"{start_loc_name} → {STATION_NAMES[arsId]} 정류장 ({distance}m)",
                                "crowded": "보통 😐",
                                "low_floor": "저상" if bus.get("busType") == "1" else "일반"
                            })
                            
        if not all_buses:
            return jsonify({"status": "empty", "message": "현재 다가오는 버스가 없습니다."})

        all_buses.sort(key=lambda x: (x['priority'], x['seconds']))
        return jsonify({"status": "success", "best_bus": all_buses[0], "bus_list": all_buses})
            
    except Exception as e:
        return jsonify({"status": "error", "message": f"🚨 통신 에러: {str(e)}"})

if __name__ == '__main__':
    app.run(debug=True, port=8080)
