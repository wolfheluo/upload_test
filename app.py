# chunk_upload_server.py
from flask import Flask, request, jsonify, render_template, send_from_directory, abort, session
import os
import logging
import secrets
import re
import uuid
import hashlib
import time
import shutil
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
import html

app = Flask(__name__)
# 設置隨機密鑰用於會話
app.config['SECRET_KEY'] = secrets.token_hex(32)

# 確保使用絕對路徑以避免路徑相關問題
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
CHUNK_FOLDER = os.path.join(BASE_DIR, 'chunked')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(CHUNK_FOLDER, exist_ok=True)

# 設置最大檔案大小
MAX_CONTENT_LENGTH = 300 * 1024 * 1024 * 1024  # 300GB
# 設置臨時檔案最長保存時間（24小時）
MAX_CHUNK_AGE = 24 * 60 * 60  # 秒

# 設置日誌記錄
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)  # 將日誌等級設置為 DEBUG
# 添加檔案處理器，將日誌保存到文件
file_handler = logging.FileHandler(os.path.join(BASE_DIR, 'upload_server.log'))
file_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
logger = logging.getLogger(__name__)
logger.addHandler(file_handler)

def allowed_file(filename):
    """現在接受任意檔案類型"""
    return True

def validate_input(input_data, pattern=None, max_length=100):
    """驗證用戶輸入，防止注入攻擊"""
    # 檢查輸入是否為 None
    if input_data is None:
        return False
    
    # 檢查長度
    if len(input_data) > max_length:
        return False
        
    # 如果提供了正則表達式模式，使用 fullmatch 確保整個字符串符合模式
    if pattern and not re.fullmatch(pattern, input_data):
        return False
        
    return True

def cleanup_old_chunks():
    """清理過期的塊文件目錄"""
    now = time.time()
    try:
        for upload_id in os.listdir(CHUNK_FOLDER):
            chunk_dir = os.path.join(CHUNK_FOLDER, upload_id)
            if not os.path.isdir(chunk_dir):
                continue
                
            # 檢查目錄的修改時間
            dir_mtime = os.path.getmtime(chunk_dir)
            age = now - dir_mtime
            
            # 如果目錄超過最大年齡，則刪除
            if age > MAX_CHUNK_AGE:
                logger.info(f"Cleaning up stale chunks folder: {upload_id} (age: {age/3600:.1f} hours)")
                try:
                    shutil.rmtree(chunk_dir)
                except Exception as e:
                    logger.error(f"Error deleting chunk directory {chunk_dir}: {str(e)}")
        
        logger.debug("Cleanup of old chunks completed")
    except Exception as e:
        logger.error(f"Error during chunks cleanup: {str(e)}")

@app.route('/')
def index():
    logger.debug("Rendering index page")
    return render_template('index.html')

@app.route('/check_chunks', methods=['POST'])
def check_chunks():
    upload_id = request.form.get('upload_id')
    logger.debug(f"Received check_chunks request with upload_id: {upload_id}")
    # 驗證 upload_id 格式
    if not validate_input(upload_id, r'^[\w\-._]+$', 200):
        return jsonify({"error": "Invalid upload ID format"}), 400

    chunk_dir = os.path.join(CHUNK_FOLDER, upload_id)
    if not os.path.exists(chunk_dir):
        return jsonify({"uploaded": []})

    # 安全獲取已上傳的塊
    try:
        uploaded_chunks = [int(name) for name in os.listdir(chunk_dir) if name.isdigit()]
    except Exception as e:
        logger.error(f"Error listing chunks: {str(e)}")
        return jsonify({"uploaded": []})

    logger.debug(f"Uploaded chunks for {upload_id}: {uploaded_chunks}")
    return jsonify({"uploaded": uploaded_chunks})

@app.route('/upload_chunk', methods=['POST'])
def upload_chunk():
    try:
        logger.debug("Received upload_chunk request")
        # 獲取並驗證參數
        upload_id = request.form.get('upload_id')
        if not validate_input(upload_id, r'^[\w\-._]+$', 200):
            return jsonify({"error": "Invalid upload ID format"}), 400

        chunk_index = request.form.get('chunk_index')
        if not validate_input(chunk_index, r'^\d+$', 10):
            return jsonify({"error": "Invalid chunk index"}), 400
        chunk_index = int(chunk_index)

        total_chunks = request.form.get('total_chunks')
        if not validate_input(total_chunks, r'^\d+$', 10):
            return jsonify({"error": "Invalid total chunks"}), 400
        total_chunks = int(total_chunks)

        filename = request.form.get('filename')
        if not validate_input(filename, None, 255):
            return jsonify({"error": "Invalid filename"}), 400

        # 確保檔案名稱是安全的
        secure_name = secure_filename(filename)
        
        # 檢查是否有文件
        if 'file' not in request.files:
            return jsonify({"error": "No file part"}), 400
        file = request.files['file']
        
        chunk_dir = os.path.join(CHUNK_FOLDER, upload_id)
        os.makedirs(chunk_dir, exist_ok=True)

        # 安全地保存文件塊
        chunk_path = os.path.join(chunk_dir, f"{chunk_index:05d}")
        file.save(chunk_path)
        
        logger.debug(f"Chunk {chunk_index} saved at {chunk_path}")
        logger.info(f"Chunk {chunk_index} of {total_chunks} saved for file {html.escape(secure_name)}")

        # 檢查是否所有塊都已上傳
        uploaded_chunks = os.listdir(chunk_dir)
        if len(uploaded_chunks) == total_chunks:
            final_path = os.path.join(UPLOAD_FOLDER, secure_name)
            with open(final_path, 'wb') as outfile:
                for i in range(total_chunks):
                    chunk_file = os.path.join(chunk_dir, f"{i:05d}")
                    if os.path.exists(chunk_file):
                        with open(chunk_file, 'rb') as infile:
                            outfile.write(infile.read())
            
            # 清理上傳的塊文件
            try:
                for chunk_file in os.listdir(chunk_dir):
                    os.remove(os.path.join(chunk_dir, chunk_file))
                os.rmdir(chunk_dir)
                logger.debug(f"Temporary chunks for {upload_id} cleaned up")
            except Exception as clean_err:
                logger.error(f"Error cleaning up chunks: {str(clean_err)}")
            
            logger.debug(f"File {secure_name} successfully assembled from chunks")
            logger.info(f"File {html.escape(secure_name)} successfully assembled from chunks")
            return jsonify({"status": "completed", "filename": secure_name})
        
        return jsonify({"status": "partial", "received_chunk": chunk_index})
    except Exception as e:
        logger.error(f"Error processing upload: {str(e)}")
        return jsonify({"status": "error", "message": "Server error"}), 500

@app.route('/finalize_upload', methods=['POST'])
def finalize_upload():
    """確認檔案上傳及處理狀態的終點"""
    try:
        logger.debug("Received finalize_upload request")
        # 獲取並驗證參數
        upload_id = request.form.get('upload_id')
        if not validate_input(upload_id, r'^[\w\-._]+$', 200):
            return jsonify({"error": "Invalid upload ID format"}), 400

        filename = request.form.get('filename')
        if not validate_input(filename, None, 255):
            return jsonify({"error": "Invalid filename"}), 400

        total_chunks = request.form.get('total_chunks')
        if not validate_input(total_chunks, r'^\d+$', 10):
            return jsonify({"error": "Invalid total chunks"}), 400
        total_chunks = int(total_chunks)

        # 確保檔案名稱是安全的
        secure_name = secure_filename(filename)
        final_path = os.path.join(UPLOAD_FOLDER, secure_name)
        
        # 檢查檔案是否已經成功合併
        if os.path.exists(final_path):
            file_size = os.path.getsize(final_path)
            file_url = f"/uploads/{secure_name}"
            logger.info(f"File {html.escape(secure_name)} finalization confirmed. Size: {file_size} bytes")
            return jsonify({
                "status": "success", 
                "filename": secure_name,
                "file_path": file_url,
                "file_size": file_size
            })
        
        # 檢查區塊檔案是否存在，可能需要手動合併
        chunk_dir = os.path.join(CHUNK_FOLDER, upload_id)
        if os.path.exists(chunk_dir):
            uploaded_chunks = [f for f in os.listdir(chunk_dir) if os.path.isfile(os.path.join(chunk_dir, f))]
            
            # 如果所有區塊都已上傳，嘗試手動合併
            if len(uploaded_chunks) == total_chunks:
                logger.debug(f"Manually merging chunks for {secure_name}")
                try:
                    with open(final_path, 'wb') as outfile:
                        for i in range(total_chunks):
                            chunk_file = os.path.join(chunk_dir, f"{i:05d}")
                            if os.path.exists(chunk_file):
                                with open(chunk_file, 'rb') as infile:
                                    outfile.write(infile.read())
                    
                    # 清理上傳的區塊檔案
                    for chunk_file in os.listdir(chunk_dir):
                        os.remove(os.path.join(chunk_dir, chunk_file))
                    os.rmdir(chunk_dir)
                    
                    file_size = os.path.getsize(final_path)
                    file_url = f"/uploads/{secure_name}"
                    logger.info(f"File {html.escape(secure_name)} manually merged. Size: {file_size} bytes")
                    return jsonify({
                        "status": "success", 
                        "filename": secure_name,
                        "file_path": file_url,
                        "file_size": file_size
                    })
                except Exception as merge_err:
                    logger.error(f"Error manually merging chunks: {str(merge_err)}")
                    return jsonify({"error": f"Failed to merge chunks: {str(merge_err)}"}), 500
            else:
                # 部分區塊尚未上傳
                return jsonify({
                    "error": f"Incomplete upload: {len(uploaded_chunks)}/{total_chunks} chunks uploaded"
                }), 400
        
        # 既沒有最終檔案也沒有區塊檔案
        logger.error(f"Finalization failed: No file or chunks found for {secure_name}")
        return jsonify({"error": "No file or chunks found"}), 404
        
    except Exception as e:
        logger.error(f"Error during finalization: {str(e)}")
        return jsonify({"error": f"Server error: {str(e)}"}), 500

@app.route('/uploads/<filename>')
def download_file(filename):
    logger.debug(f"Download request for file: {filename}")
    """提供上傳後的檔案下載功能"""
    # 驗證文件名
    if not validate_input(filename, None, 255):
        abort(404)
    secure_name = secure_filename(filename)
    
    # 檢查文件是否存在
    if not os.path.exists(os.path.join(UPLOAD_FOLDER, secure_name)):
        abort(404)
        
    logger.debug(f"File {secure_name} found and ready for download")
    return send_from_directory(UPLOAD_FOLDER, secure_name)

@app.route('/files')
def list_files():
    logger.debug("Listing all uploaded files")
    """列出所有已上傳的檔案"""
    try:
        files = os.listdir(UPLOAD_FOLDER)
        # 過濾掉任何不安全的文件名
        files = [f for f in files if validate_input(f, None, 255)]
        logger.debug(f"Files available: {files}")
        return render_template('files.html', files=files)
    except Exception as e:
        logger.error(f"Error listing files: {str(e)}")
        return render_template('files.html', files=[])

@app.route('/admin/cleanup', methods=['GET'])
def admin_cleanup():
    """管理員手動執行清理功能的接口"""
    try:
        cleanup_old_chunks()
        return jsonify({"status": "success", "message": "Cleanup completed"})
    except Exception as e:
        logger.error(f"Error during manual cleanup: {str(e)}")
        return jsonify({"status": "error", "message": str(e)})

@app.errorhandler(413)
def request_entity_too_large(error):
    """處理文件過大的錯誤"""
    return jsonify({"error": "File too large"}), 413

@app.errorhandler(403)
def forbidden(error):
    """處理 CSRF 錯誤"""
    return jsonify({"error": "CSRF validation failed"}), 403

# 在應用啟動時清理舊的臨時文件
# 替換 @app.before_first_request 為以下實現方式
def before_first_request():
    """在應用啟動時執行清理過程"""
    cleanup_old_chunks()
    logger.info("Initial cleanup of stale chunks completed")

# 設置最大內容長度
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

if __name__ == '__main__':
    # 在啟動應用前執行清理操作
    with app.app_context():
        before_first_request()
    app.run(debug=False, port=5000)  # 生產環境中設置 debug=False
