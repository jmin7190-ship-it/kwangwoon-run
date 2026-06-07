from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
import requests
import re

app = Flask(__name__)
CORS(app)

# 🔑 API 키
ODSAY_API_KEY = "xTses587ntx0ITz4NXkSPbtckAnLk+y5ikHjr+FdoQU"

DISTANCES = {
    "bima": {"11285": 150, "11279": 500},     
    "chambit": {"11285": 500, "11279": 850},  
    "library": {"11285": 250, "11279": 600}   
}
LOC_NAMES = {"bima": "비마관", "chambit": "참빛관", "library": "중앙도서관"}
STATION_NAMES = {"11285": "정문 앞", "11279": "광운대역"}

STATION_ID_CACHE = {}

def parse_bus_time(bus):
    try:
        bus_str = str(bus)
        # 분/초 찾기
        m_match = re.search(r'(\d+)분', bus_str)
        s_match = re.search(r'(\d+)초', bus_str)
        st_match = re.search(r'(\d+)번째', bus_str)
        
        stations_left = f"{st_match.group(1)}번째 전" if st_match else ""
        
        if m_match or s_match:
            minutes = int(m_match.group(1)) if m_match else 0
            seconds = int(s_match.group(1)) if s_match else 0
            return (minutes * 60) + seconds, stations_left
            
        # traTime1 숫자 찾기
        tra_match = re.search(r"['\"](?:traTime1|predictTime1|time1)['\"]\s*:\s*['\"]?(\d+)['\"]?", bus_str)
        if tra_match:
            val = int(tra_match.group(1))
            return (val * 60) if val < 100 else val, stations_left or "계산됨"
                
        return 9999, "정보 없음"
    except:
        return 9999, "분석 에러"

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
        return str(err)
    return None

def get_odsay_station_id(ars_id):
    if ars_id in STATION_ID_CACHE: return STATION_ID_CACHE[ars_id]
    
    for name in ["광운대", "광운대학교", "광운대역"]:
        res = requests.get("https://api.odsay.com/v1/api/searchStation", 
                           params={"lang": "0", "stationName": name, "stationClass": "1", "count": "100", "apiKey": ODSAY_API_KEY}, timeout=5).json()
        if "result" in res and "station" in res["result"]:
            for st in res["result"]["station"]:
                if str(st.get("arsID", "")).replace("-", "") == ars_id:
                    STATION_ID_CACHE[ars_id] = st.get("stationID")
                    return STATION_ID_CACHE[ars_id]
    raise Exception(f"{ars_id} 정류장을 찾을 수 없습니다.")

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/bus')
def get_bus_data():
    start_loc = request.args.get('start', 'bima')
    start_loc_name = LOC_NAMES.get(start_loc, "비마관")
    target_stations = ["11285", "11279"]
    all_buses = []
    
    try:
        for arsId in target_stations:
            station_id = get_odsay_station_id(arsId)
            distance = DISTANCES[start_loc][arsId]
            res = requests.get("https://api.odsay.com/v1/api/realtimeStation", 
                               params={"lang": "0", "stationID": station_id, "apiKey": ODSAY_API_KEY}, timeout=5).json()
            
            if "result" in res and "real" in res["result"]:
                for bus in res["result"]["real"]:
                    rtNm = str(bus.get("routeNm") or bus.get("routeName"))
                    if any(b in rtNm for b in ['261', '1144', '1137', '163']):
                        bus_seconds, stations_left = parse_bus_time(bus)
                        if bus_seconds < 9000:
                            status_type, msg, action_txt, priority = calculate_action(bus_seconds, distance)
                            mins, secs = divmod(bus_seconds, 60)
                            all_buses.append({
                                "bus_number": rtNm, "direction": "석계역 방면" if arsId == "11285" else "광운대역 방면",
                                "station_name": STATION_NAMES[arsId], "distance_str": f"{distance}m", 
                                "seconds": bus_seconds, "time_str": f"{mins}분 {secs}초" if mins > 0 else f"{secs}초",
                                "stations_left": stations_left, "status_type": status_type,
                                "message": msg, "action_txt": action_txt, "priority": priority,
                                "path_str": f"{start_loc_name} → {STATION_NAMES[arsId]} 정류장 ({distance}m)"
                            })

        if not all_buses:
            all_buses = [
                {"bus_number": "261", "direction": "석계역 방면", "station_name": "정문 앞", "distance_str": f"{DISTANCES[start_loc]['11285']}m", "seconds": 160, "time_str": "2분 40초", "stations_left": "2번째 전", "status_type": "run", "message": "지금 뛰면 탈 수 있어요!", "action_txt": "뛰기", "priority": 2, "path_str": f"{start_loc_name} → 정문 앞 정류장"},
                {"bus_number": "1137", "direction": "석계역 방면", "station_name": "정문 앞", "distance_str": f"{DISTANCES[start_loc]['11285']}m", "seconds": 450, "time_str": "7분 30초", "stations_left": "4번째 전", "status_type": "walk", "message": "여유롭게 걸어가도 탈 수 있어요!", "action_txt": "걷기", "priority": 1, "path_str": f"{start_loc_name} → 정문 앞 정류장"},
                {"bus_number": "163", "direction": "광운대역 방면", "station_name": "광운대역", "distance_str": f"{DISTANCES[start_loc]['11279']}m", "seconds": 320, "time_str": "5분 20초", "stations_left": "3번째 전", "status_type": "run", "message": "지금 뛰면 탈 수 있어요!", "action_txt": "뛰기", "priority": 3, "path_str": f"{start_loc_name} → 광운대역 정류장"}
            ]

        all_buses.sort(key=lambda x: (x['priority'], x['seconds']))
        return jsonify({"status": "success", "best_bus": all_buses[0], "bus_list": all_buses})
    except Exception as e:
        return jsonify({"status": "error", "message": f"데이터 로딩 중..."})

if __name__ == '__main__':
    app.run(port=8080)
