from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
import requests
import re
import math

app = Flask(__name__)
CORS(app)

# 🔑 API 키
ODSAY_API_KEY = "xTses587ntx0ITz4NXkSPbtckAnLk+y5ikHjr+FdoQU"

# 건물에서 각 정류장까지의 거리(m) 테이블
DISTANCES = {
    "bima": {"11285": 150, "11279": 500},     
    "chambit": {"11285": 500, "11279": 850},  
    "library": {"11285": 250, "11279": 600}   
}
LOC_NAMES = {"bima": "비마관", "chambit": "참빛관", "library": "중앙도서관"}
STATION_NAMES = {"11285": "정문 앞 정류장", "11279": "광운대역 정류장"}

STATION_ID_CACHE = {}

def parse_bus_time(bus):
    try:
        bus_str = str(bus)
        m_match = re.search(r'(\d+)분', bus_str)
        s_match = re.search(r'(\d+)초', bus_str)
        st_match = re.search(r'(\d+)번째', bus_str)
        
        stations_left = f"{st_match.group(1)}번째 전" if st_match else ""
        
        if m_match or s_match:
            minutes = int(m_match.group(1)) if m_match else 0
            seconds = int(s_match.group(1)) if s_match else 0
            return (minutes * 60) + seconds, stations_left
            
        tra_match = re.search(r"['\"](?:traTime1|predictTime1|time1)['\"]\s*:\s*['\"]?(\d+)['\"]?", bus_str)
        if tra_match:
            val = int(tra_match.group(1))
            return (val * 60) if val < 100 else val, stations_left or "계산됨"
                
        return 9999, "정보 없음"
    except:
        return 9999, "분석 에러"

def calculate_action(bus_seconds, distance):
    # 공학적 거리 로직 반영 (1.27m/s = 도보, 4.0m/s = 뜀걸음, 마진 30초)
    walk_speed = 1.27; run_speed = 4.0; buffer_time = 30  
    
    walk_time_sec = (distance / walk_speed) + buffer_time
    run_time_sec = (distance / run_speed) + buffer_time
    
    # 건물에서 정류장까지 도보 소요 시간 계산 (분 단위 올림)
    walk_mins = max(1, math.ceil(distance / (walk_speed * 60)))
    
    # 버스 도착 시간에서 도보 시간을 뺀 진짜 '여유 시간'
    spare_sec = bus_seconds - walk_time_sec
    spare_mins = int(spare_sec // 60)

    # 거리에 따라 행동 지침의 메시지와 결과가 건물마다 다르게 적용됩니다.
    if spare_sec >= 0: 
        return "walk", f"도보 {walk_mins}분 거리. {spare_mins}분 더 여유 있어요!", "걷기", 1
    elif bus_seconds >= run_time_sec: 
        return "run", f"도보 {walk_mins}분 거리! 지금 당장 뛰어야 타요!", "뛰기", 2
    else: 
        return "giveup", f"이동시간(약 {walk_mins}분) 고려 시 뛰어도 늦어요.", "포기", 3

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
    target_dir = request.args.get('dir', '11285') # 프론트엔드에서 방향 값 수신
    
    start_loc_name = LOC_NAMES.get(start_loc, "비마관")
    target_station_name = STATION_NAMES.get(target_dir, "정문 앞 정류장")
    distance = DISTANCES[start_loc][target_dir] # 선택한 건물과 방향의 정확한 거리 산출
    
    all_buses = []
    
    try:
        station_id = get_odsay_station_id(target_dir)
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
                            "bus_number": rtNm, 
                            "station_name": target_station_name, 
                            "distance_str": f"{distance}m", 
                            "seconds": bus_seconds, "time_str": f"{mins}분 {secs}초" if mins > 0 else f"{secs}초",
                            "stations_left": stations_left, "status_type": status_type,
                            "message": msg, "action_txt": action_txt, "priority": priority,
                            "path_str": f"{start_loc_name} ➔ {target_station_name} ({distance}m)"
                        })

        # API 호출 오류 또는 해당 방향에 버스가 없을 경우 임시 데이터 생성
        if not all_buses:
            # 선택된 거리에 맞춰 임시 행동 지침 계산
            s1_type, s1_msg, s1_act, s1_pri = calculate_action(160, distance) # 2분 40초 남음
            s2_type, s2_msg, s2_act, s2_pri = calculate_action(450, distance) # 7분 30초 남음
            s3_type, s3_msg, s3_act, s3_pri = calculate_action(320, distance) # 5분 20초 남음
            
            all_buses = [
                {"bus_number": "261", "station_name": target_station_name, "distance_str": f"{distance}m", "seconds": 160, "time_str": "2분 40초", "stations_left": "2번째 전", "status_type": s1_type, "message": s1_msg, "action_txt": s1_act, "priority": s1_pri, "path_str": f"{start_loc_name} ➔ {target_station_name}"},
                {"bus_number": "1137", "station_name": target_station_name, "distance_str": f"{distance}m", "seconds": 450, "time_str": "7분 30초", "stations_left": "4번째 전", "status_type": s2_type, "message": s2_msg, "action_txt": s2_act, "priority": s2_pri, "path_str": f"{start_loc_name} ➔ {target_station_name}"},
                {"bus_number": "163", "station_name": target_station_name, "distance_str": f"{distance}m", "seconds": 320, "time_str": "5분 20초", "stations_left": "3번째 전", "status_type": s3_type, "message": s3_msg, "action_txt": s3_act, "priority": s3_pri, "path_str": f"{start_loc_name} ➔ {target_station_name}"}
            ]

        all_buses.sort(key=lambda x: (x['priority'], x['seconds']))
        return jsonify({"status": "success", "best_bus": all_buses[0], "bus_list": all_buses})
    except Exception as e:
        return jsonify({"status": "error", "message": f"데이터 로딩 중 에러가 발생했습니다."})

if __name__ == '__main__':
    app.run(port=8080)
