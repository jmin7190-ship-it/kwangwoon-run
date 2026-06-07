from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
import requests
import re

app = Flask(__name__)
CORS(app)

ODSAY_API_KEY = "xTses587ntx0ITz4NXkSPbtckAnLk+y5ikHjr+FdoQU"

# 건물별 정류장 이동거리(m) - 정문(11285), 광운대역(11279)
DISTANCES = {
    "bima": {"11285": 150, "11279": 500},
    "chambit": {"11285": 500, "11279": 850},
    "library": {"11285": 250, "11279": 600}
}

def calculate_action(bus_seconds, distance):
    # 이동시간(초) = (거리 / 속도) + 버퍼30초
    walk_time = (distance / 1.27) + 30
    run_time = (distance / 4.0) + 30
    
    if bus_seconds >= walk_time: return "walk", "여유롭게 걸어가도 탈 수 있어요!", "걷기", 1
    elif bus_seconds >= run_time: return "run", "지금 뛰면 탈 수 있어요!", "뛰기", 2
    else: return "giveup", "뛰어도 늦어요. 다음 버스를 노리세요!", "포기", 3

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/bus')
def get_bus_data():
    bldg = request.args.get('bldg', 'bima')
    arsId = request.args.get('station', '11285')
    dist = DISTANCES.get(bldg, {}).get(arsId, 300)
    
    # 실제 API 로직 및 가짜 데이터 방어막
    try:
        # (기존 로직 사용)
        # 결과값에 bus_seconds 추출 후
        status, msg, action_txt, priority = calculate_action(bus_seconds, dist)
        return jsonify({
            "status": "success", "bus_seconds": bus_seconds,
            "action": action_txt, "message": msg, "priority": priority
        })
    except:
        return jsonify({"status": "error", "message": "데이터 로딩 중"})

if __name__ == '__main__':
    app.run(port=8080)
