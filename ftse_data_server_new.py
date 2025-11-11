import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request
from flask_cors import CORS
import time
import logging
import threading
from datetime import datetime
import pytz

# 設置日誌
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('ftse_server.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=True)

# 全局變量
ftse_data = None
last_update_time = 0
ftse_lock = threading.Lock()

class FTSEDataError(Exception):
    """自定義異常類，用於處理富台指數據相關錯誤"""
    pass

def get_taipei_time():
    """獲取台北時間"""
    taipei_tz = pytz.timezone('Asia/Taipei')
    return datetime.now(taipei_tz)

def is_market_hours():
    """檢查是否在交易時間內"""
    now = get_taipei_time()
    
    # 如果是週末，返回False
    if now.weekday() >= 5:
        return False
    
    # 定義交易時間（8:45 - 13:45）
    market_start = now.replace(hour=8, minute=45, second=0, microsecond=0)
    market_end = now.replace(hour=13, minute=45, second=0, microsecond=0)
    
    return market_start <= now <= market_end

def round_to_quarter(value):
    """將小數點後四捨五入到 .0, .25, .5, .75"""
    try:
        # 分離整數部分和小數部分
        integer_part = int(value)
        decimal_part = value - integer_part
        
        # 根據小數部分四捨五入
        if decimal_part < 0.125:
            rounded_decimal = 0
        elif decimal_part < 0.375:
            rounded_decimal = 0.25
        elif decimal_part < 0.625:
            rounded_decimal = 0.5
        elif decimal_part < 0.875:
            rounded_decimal = 0.75
        else:
            # 進位到下一個整數
            integer_part += 1
            rounded_decimal = 0
        
        result = integer_part + rounded_decimal
        logger.debug(f"四捨五入: {value} -> {result}")
        return result
    except Exception as e:
        logger.error(f"四捨五入錯誤: {str(e)}")
        return value

def get_ftse_data_from_histock():
    """從HiStock網站獲取富台指數據"""
    global ftse_data, last_update_time
    
    try:
        logger.info("嘗試從HiStock獲取富台指數據...")
        timestamp = int(time.time())
        url = f"https://histock.tw/index-tw/TWN?_nocache={timestamp}"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'lxml')
        
        # 尋找價格信息區
        price_info = soup.find('ul', class_='priceinfo')
        if not price_info:
            raise FTSEDataError("無法找到價格資訊區域")
            
        # 提取價格
        try:
            price_element = price_info.find('span', id='Price1_lbTPrice').find('span', class_=['clr-rd', 'clr-gr'])
            if not price_element:
                raise FTSEDataError("無法找到價格元素")
            price = float(price_element.text.strip().replace(',', ''))
            # 將價格四捨五入到 .0, .25, .5, .75
            price = round_to_quarter(price)
        except (ValueError, AttributeError):
            raise FTSEDataError("無法解析價格")
            
        # 提取漲跌
        try:
            change_element = price_info.find('span', id='Price1_lbTChange').find('span', class_=['clr-rd', 'clr-gr'])
            if not change_element:
                raise FTSEDataError("無法找到漲跌元素")
            change_text = change_element.text.strip()
            # 保留原始數字的正負號
            change = float(change_text.replace('▼', '').replace('▲', '').replace(',', ''))
            # 如果數字本身就帶有負號，不需要額外處理
            if not change_text.startswith('-') and ('▼' in change_text or change_element.get('class', [''])[0] == 'clr-gr'):
                change = -change
        except (ValueError, AttributeError):
            raise FTSEDataError("無法解析漲跌")
            
        # 提取漲跌百分比
        try:
            percent_element = price_info.find('span', id='Price1_lbTPercent').find('span', class_=['clr-rd', 'clr-gr'])
            if not percent_element:
                raise FTSEDataError("無法找到漲跌百分比元素")
            percent_text = percent_element.text.strip().replace('%', '')
            # 直接使用文本中的正負號，保留+號
            change_percent = float(percent_text)
            # 只有當數字本身沒有負號，且有下跌標記時才轉為負數
            if not percent_text.startswith('-') and ('▼' in percent_element.text or percent_element.get('class', [''])[0] == 'clr-gr'):
                change_percent = -change_percent
        except (ValueError, AttributeError):
            raise FTSEDataError("無法解析漲跌百分比")
            
        # 調整富台指價格（減 0.05）
        adjusted_price = price 
        
        # 計算台指期價格（使用調整後的價格）
        tx_price = calculate_tx_price(adjusted_price)
        tx_change = calculate_tx_change(adjusted_price)
        
        # 更新數據
        update_ftse_data(adjusted_price, change, change_percent, "HiStock網站", tx_price, tx_change)
        return True
        
    except requests.RequestException as e:
        logger.error(f"HTTP請求錯誤: {str(e)}")
        return handle_data_error(f"網絡錯誤: {str(e)}")
    except ValueError as e:
        logger.error(f"數據解析錯誤: {str(e)}")
        return handle_data_error(f"數據格式錯誤: {str(e)}")
    except FTSEDataError as e:
        logger.error(f"富台指數據錯誤: {str(e)}")
        return handle_data_error(str(e))
    except Exception as e:
        logger.error(f"未預期的錯誤: {str(e)}", exc_info=True)
        return handle_data_error(f"系統錯誤: {str(e)}")

def calculate_tx_price(ftse_price):
    """根據富時台指計算台指期貨價格"""
    try:
        # 使用更精確的轉換係數
        #  12.33668058219564
        conversion_factor = 12.28065515714918
        tx_price = ftse_price * conversion_factor
        tx_price = round(tx_price, 0)
        
        logger.debug(f"計算台指期價格: {tx_price} (富台指: {ftse_price}, 係數: {conversion_factor})")
        return tx_price
    except Exception as e:
        logger.error(f"計算台指期價格時出錯: {str(e)}")
        raise

def calculate_tx_change(ftse_price):
    """計算台指期與基準值的差距"""
    try:
        base_tx = 27556  # 基準台指期價格
        tx_price = calculate_tx_price(ftse_price)
        tx_change = tx_price - base_tx
        tx_change = round(tx_change, 0)
        
        logger.debug(f"計算台指期跌點: {tx_change} (基準: {base_tx}, 當前: {tx_price})")
        return tx_change
    except Exception as e:
        logger.error(f"計算台指期跌點時出錯: {str(e)}")
        raise

def update_ftse_data(price, change, change_percent, source, tx_price, tx_change):
    """更新富台指數據"""
    global ftse_data, last_update_time
    
    current_time = time.time()
    taipei_time = get_taipei_time().strftime('%Y-%m-%d %H:%M:%S')
    
    with ftse_lock:
        ftse_data = {
            'code': 'TWN',
            'name': '富時台指',
            'price': price,
            'change': change,
            'changePercent': change_percent,
            'timestamp': current_time,
            'taipei_time': taipei_time,
            'source': source,
            'tx_price': tx_price,
            'tx_change': tx_change,
            'is_market_hours': is_market_hours()
        }
        last_update_time = current_time
        
    logger.info(
        f"更新數據完成 | 價格: {price:,.2f} | 漲跌: {change:+.2f} ({change_percent:+.2f}%) | "
        f"台指期: {tx_price:,.0f} (變動: {tx_change:+.0f}) | 來源: {source}"
    )

def handle_data_error(error_message):
    """處理數據錯誤，返回上一次有效數據或預設值"""
    global ftse_data, last_update_time
    
    with ftse_lock:
        if ftse_data and time.time() - last_update_time < 300:  # 5分鐘內的數據仍然有效
            logger.info("使用最後有效數據")
            ftse_data['error'] = error_message
            return True
            
    # 使用預設數據
    default_price = 1637.5
    logger.info(f"使用預設數據 (價格: {default_price})")
    
    with ftse_lock:
        ftse_data = {
            'code': 'TWN',
            'name': '富時台指',
            'price': default_price,
            'change': -68.3,
            'changePercent': -4.0,
            'timestamp': time.time(),
            'taipei_time': get_taipei_time().strftime('%Y-%m-%d %H:%M:%S'),
            'source': '預設數據',
            'tx_price': calculate_tx_price(default_price),
            'tx_change': calculate_tx_change(default_price),
            'error': error_message,
            'is_market_hours': is_market_hours()
        }
        last_update_time = time.time()
    return False

def get_ftse_data():
    """獲取富台指數據"""
    global ftse_data, last_update_time
    
    with ftse_lock:
        if ftse_data:
            # 在交易時間內每20秒更新，非交易時間每5分鐘更新
            update_interval = 20 if is_market_hours() else 300
            if time.time() - last_update_time > update_interval:
                logger.info(f"數據已超過{update_interval}秒，重新獲取")
                threading.Thread(target=get_ftse_data_from_histock, daemon=True).start()
            return ftse_data
    
    logger.info("首次獲取數據")
    get_ftse_data_from_histock()
    
    with ftse_lock:
        return ftse_data

@app.route('/api/ftse', methods=['GET'])
def api_get_ftse_data():
    """API端點獲取富台指數據"""
    try:
        force_refresh = request.args.get('refresh', 'false').lower() == 'true'
        data = get_ftse_data()
        
        if force_refresh or (time.time() - data['timestamp'] > (20 if is_market_hours() else 300)):
            logger.info("強制更新數據...")
            get_ftse_data_from_histock()
            data = get_ftse_data()
        
        # 添加額外資訊
        data['request_time'] = time.time()
        data['server_time'] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
        
        return jsonify(data)
    except Exception as e:
        logger.error(f"API請求處理錯誤: {str(e)}", exc_info=True)
        return jsonify({
            'error': str(e),
            'timestamp': time.time(),
            'server_time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
        }), 500

def run_background_update():
    """後台更新任務"""
    while True:
        try:
            # 根據是否在交易時間決定更新頻率
            update_interval = 10 if is_market_hours() else 60
            time.sleep(update_interval)
            
            logger.debug(f"後台更新任務執行中 (間隔: {update_interval}秒)")
            get_ftse_data_from_histock()
            
        except Exception as e:
            logger.error(f"後台更新任務錯誤: {str(e)}", exc_info=True)
            time.sleep(5)  # 發生錯誤時等待5秒

# 初始化數據和後台更新（在應用啟動時執行）
logger.info("啟動富台指數據服務...")

# 確保必要的套件已安裝
try:
    import pytz
    import bs4
except ImportError as e:
    logger.error(f"缺少必要套件: {str(e)}")
    logger.info("請執行: pip install pytz beautifulsoup4 lxml")
    exit(1)

# 初始化數據
get_ftse_data_from_histock()

# 啟動後台更新線程
updater_thread = threading.Thread(target=run_background_update, daemon=True)
updater_thread.start()
logger.info("後台數據更新線程已啟動")

if __name__ == '__main__':
    # 啟動服務（支援 Railway 的 PORT 環境變數）
    import os
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False)
