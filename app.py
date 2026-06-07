from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
import requests
import re

app = Flask(__name__)
CORS(app)

# 🚨 내 클라우드 서버 IP 확인 스파이
try:
    my_ip = requests.get("https://api.ipify.org", timeout=3).text
    print(f"\n\n🚀🚀🚀 내 클라우드 서버 IP: {my_ip} 🚀🚀🚀\n\n", flush=True)
except:
    pass

# 🔑 제민님의 새 API 키
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
    """
    버스 데이터 덩어리를 통째로 받아서, 
    어디에 숨어있든 도착 시간(초)을 무조건 찾아내는 궁극의 번역기
    """
    try:
        # 1. ODsay가 아주 친절하게 'traTime1(초 단위)'를 따로 준 경우 (1순위)
        if bus.get("traTime1"):
            sec = int(bus["traTime1"])
            if sec > 0:
                # 몇 번째 전인지는 arrmsg1에서 슬쩍 가져옵니다
                arrmsg1 = str(bus.get("arrmsg1", ""))
                st_match = re.search(r'(\d+)번째', arrmsg1)
                stations_left = f"{st_match.group(1)}번째 전" if st_match else "계산됨"
                return sec, stations_left
                
        # 2. ODsay가 'predictTime1(분 단위)'를 준 경우 (2순위)
        if bus.get("predictTime1"):
            mins = int(bus["predictTime1"])
            if mins > 0:
                arrmsg1 = str(bus.get("arrmsg1", ""))
                st_match = re.search(r'(\d+)번째', arrmsg1)
                stations_left = f"{st_match.group(1)}번째 전" if st_match else "계산됨"
                return mins * 60, stations_left

        # 3. arrmsg1 텍스트(예: "5분 30초 후")를 직접 해독해야 하는 경우 (3순위)
        arrmsg1 = bus.get("arrmsg1")
        if arrmsg1:
            # 만약 딕셔너리 포장지면 알맹이(#text)만 쏙 빼냅니다
            if isinstance(arrmsg1, dict):
                arrmsg1 = arrmsg1.get("#text") or str(arrmsg1)
                
            full_str = str(arrmsg1).strip()
            
            if "종료" in full_str or "대기" in full_str: return 9999, "종료/대기"
            if "곧 도착" in full_str or "운행중" in full_str: return 60, "곧 도착"
            
            m_match = re.search(r'(\d+)분', full_str)
            s_match = re.search(r'(\d+)초', full_str)
            st_match = re.search(r'(\d+)번째', full_str)
            
            stations_left = f"{st_match.group(1)}번째 전" if st_match else ""
            
            if m_match or s_match:
                minutes = int(m_match.group(1)) if m_match else 0
                seconds = int(s_match.group(1)) if s_match else 0
                return (minutes * 60) + seconds, stations_left

        # 위 3가지 모두 실패했다면? -> 현재 이 노선에 오고 있는 버스가 없음 (유령 버스)
        return 9999, "정보 없음"
        
    except Exception as e:
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
                        # 🚨 arrmsg1 문자열 대신 bus 덩어리를 통째로 넘겨서 분석합니다!
                        bus_seconds, stations_left = parse_bus_time(bus)
                        
                        # 🚨 9999초(166분 유령 버스)를 다시 완벽하게 숨기기 위해 필터를 9000으로 원상복구했습니다!
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
