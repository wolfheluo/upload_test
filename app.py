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
import magic

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
    """檢查檔案類型是否被允許上傳
    只允許安全的檔案類型，例如圖片、文檔等"""
    # 定義允許的副檔名列表
    ALLOWED_EXTENSIONS = {
        # 文檔
        'pdf', 'doc', 'docx', 'txt', 'rtf', 'odt', 'xls', 'xlsx', 'ppt', 'pptx',
        # 圖片
        'jpg', 'jpeg', 'png', 'gif', 'bmp', 'svg', 'webp',
        # 音頻
        'mp3', 'wav', 'ogg', 'flac',
        # 視頻
        'mp4', 'avi', 'mov', 'mkv', 'webm',
        # 壓縮檔
        'zip', 'rar', '7z', 'tar', 'gz' , 'tgz', 'bz2', 'xz',
        # 其他類型
        'csv', 'json', 'xml', 'html', 'css', 'js', 'txt', 'log', 'md', 'yaml', 'yml'
    }
    # 取得副檔名並檢查
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

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

def scan_file_for_malware(file_path):
    """
    掃描檔案是否包含惡意內容
    返回 True 如果檔案疑似惡意，False 如果檔案安全
    """
    try:
        # 使用 python-magic 檢測文件類型
        mime = magic.Magic(mime=True)
        file_type = mime.from_file(file_path)
        logger.debug(f"File {os.path.basename(file_path)} detected as: {file_type}")
        
        # 危險的 MIME 類型列表
        dangerous_mimes = [
            'application/x-dosexec',       # Windows 可執行檔
            'application/x-executable',    # Linux 可執行檔
            'application/x-sharedlib',     # 共享庫
            'application/x-msdos-program', # MS-DOS 程式
            'application/x-msdownload',    # Windows DLL
            'text/x-script',               # 各種腳本
            'text/x-shellscript',          # Shell 腳本
            'text/x-perl',                 # Perl 腳本
            'text/x-python',               # Python 腳本
            'text/x-php',                  # PHP 腳本
            'application/x-javascript'     # JavaScript
        ]
        
        # 檢查 MIME 類型
        if any(mime in file_type for mime in dangerous_mimes):
            logger.warning(f"潛在危險文件類型: {file_type} for {os.path.basename(file_path)}")
            return True
            
        # 讀取文件內容進行進一步分析
        with open(file_path, 'rb') as f:
            content = f.read(1024 * 1024)  # 讀取前 1MB 進行分析
            
            # 檢查是否包含可執行代碼的特徵
            if file_type.startswith('application/zip') or file_type.startswith('application/x-rar'):
                # 壓縮檔特殊處理，可以檢查是否包含可執行檔
                import zipfile
                import rarfile
                try:
                    if file_type.startswith('application/zip'):
                        with zipfile.ZipFile(file_path) as zip_ref:
                            file_list = zip_ref.namelist()
                    else:
                        with rarfile.RarFile(file_path) as rar_ref:
                            file_list = rar_ref.namelist()
                            
                    # 檢查壓縮檔中的文件
                    executable_extensions = ['.exe', '.bat', '.cmd', '.ps1', '.vbs', '.js', '.jar', '.sh', '.php']
                    for f_name in file_list:
                        if any(f_name.lower().endswith(ext) for ext in executable_extensions):
                            logger.warning(f"壓縮檔中發現可執行檔: {f_name}")
                            return True
                except Exception as e:
                    logger.error(f"檢查壓縮檔時出錯: {str(e)}")
            
            # 檢查文件頭是否為可執行檔
            executable_signatures = [
                b'MZ',           # Windows PE
                b'\x7FELF',      # Linux ELF
                b'\xCA\xFE\xBA\xBE', # Java Class
                b'\xCF\xFA\xED\xFE', # Mach-O (macOS)
                b'#!/',          # Unix 腳本
                b'<?php',        # PHP 文件
                b'<script'       # JavaScript in HTML
            ]
            
            for sig in executable_signatures:
                if content.startswith(sig):
                    logger.warning(f"文件包含可執行格式特徵: {os.path.basename(file_path)}")
                    return True
            
            # 檢查文件內容是否包含危險模式
            dangerous_patterns = [
                rb'system\s*\(',        # 系統命令執行
                rb'exec\s*\(',          # 程式執行
                rb'eval\s*\(',          # 代碼評估
                rb'ProcessBuilder',     # Java 進程建立
                rb'Runtime\.getRuntime\(\)\.exec', # Java 命令執行
                rb'<\?php.*system\s*\(', # PHP 系統命令
                rb'powershell',         # PowerShell 命令
                rb'cmd\.exe',           # Windows 命令提示符
                rb'bash -i',            # Bash shell
                rb'nc -e',              # Netcat 反向 shell
                rb'python -c',          # Python 命令執行
            ]
            
            for pattern in dangerous_patterns:
                if re.search(pattern, content, re.IGNORECASE):
                    logger.warning(f"文件內容包含可疑指令: {os.path.basename(file_path)}")
                    return True
            
            # 計算檔案 MD5 雜湊，可用於與已知惡意檔案資料庫比對
            file_hash = hashlib.md5(content).hexdigest()
            logger.debug(f"File hash: {file_hash}")
            
            # 這裡可以添加已知惡意檔案的雜湊值比對
            known_malware_hashes = [
                # 添加已知惡意檔案的雜湊值
            ]
            
            if file_hash in known_malware_hashes:
                logger.warning(f"檔案雜湊匹配已知惡意檔案: {file_hash}")
                return True
            
        # 未檢測到惡意內容
        return False
    except Exception as e:
        logger.error(f"掃描檔案時出錯: {str(e)}")
        # 出錯時為安全起見，將檔案視為可疑
        return True

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

        # 檢查檔案類型是否允許
        if not allowed_file(filename):
            logger.warning(f"文件類型不允許: {html.escape(filename)}")
            return jsonify({"error": "文件類型不允許上傳"}), 400

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
            
            # 掃描檔案是否為惡意檔案
            if scan_file_for_malware(final_path):
                logger.warning(f"發現惡意檔案: {html.escape(secure_name)}")
                # 刪除惡意檔案
                os.remove(final_path)
                return jsonify({"error": "檔案可能包含惡意程式碼，已被拒絕"}), 400
            
            # 為上傳的檔案設置安全權限 (僅允許讀取，不允許執行)
            try:
                os.chmod(final_path, 0o644) # 設置為僅讀寫，不可執行
            except Exception as perm_err:
                logger.error(f"設置檔案權限錯誤: {str(perm_err)}")
            
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
