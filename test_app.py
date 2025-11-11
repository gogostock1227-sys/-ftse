from flask import Flask, jsonify
import time

app = Flask(__name__)

@app.route('/')
def index():
    return jsonify({'status': 'ok', 'message': 'Test server is running'})

@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'timestamp': time.time()})

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port)
