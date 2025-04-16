// 檔案上傳處理相關邏輯
let uploadTasks = []; // 儲存所有上傳任務
const maxConcurrentUploads = 3; // 並行上傳檔案數量限制
let totalFilesCompleted = 0; // 已完成的檔案計數

async function startUpload() {
    const fileInput = document.getElementById('fileInput');
    const statusEl = document.getElementById('status');
    const captchaInput = document.getElementById('captchaInput');
    const uploadButton = document.getElementById('uploadButton');
    
    // 驗證碼驗證
    const correctCaptcha = '54552253';
    if (captchaInput.value !== correctCaptcha) {
        statusEl.innerText = '錯誤：驗證碼不正確';
        statusEl.classList.add('error');
        return;
    }
    
    const files = fileInput.files;
    if (!files || files.length === 0) {
        statusEl.innerText = '請選擇至少一個檔案';
        return;
    }
    
    statusEl.innerText = `準備上傳 ${files.length} 個檔案...`;
    statusEl.classList.remove('error');
    statusEl.classList.remove('success');
    
    // 禁用上傳按鈕，避免重複上傳
    uploadButton.disabled = true;
    
    // 顯示選擇的檔案列表
    const fileListEl = document.getElementById('fileList');
    fileListEl.innerHTML = '';
    
    // 重置進度條和上傳任務
    resetProgressBar();
    uploadTasks = [];
    totalFilesCompleted = 0;
    
    // 對每個檔案建立上傳任務
    for (let i = 0; i < files.length; i++) {
        const file = files[i];
        
        // 限制檔案大小 (300GB)
        const maxSize = 300 * 1024 * 1024 * 1024;
        if (file.size > maxSize) {
            addFileToList(file, '檔案大小超過限制 (300GB)');
            continue;
        }
        
        // 檢查檔案類型是否允許
        if (!isFileTypeAllowed(file.name)) {
            addFileToList(file, '檔案類型不允許上傳');
            continue;
        }
        
        // 將檔案添加到列表
        const fileItem = addFileToList(file, '等待上傳...');
        
        // 創建上傳任務
        uploadTasks.push({
            file,
            fileElement: fileItem,
            status: 'pending', // pending, uploading, completed, failed
            index: i
        });
    }
    
    // 開始處理上傳隊列
    await processUploadQueue();
    
    // 啟用上傳按鈕
    uploadButton.disabled = false;
    
    // 更新總狀態
    if (totalFilesCompleted === uploadTasks.length) {
        statusEl.innerText = `✅ 所有檔案上傳完成！共 ${totalFilesCompleted} 個檔案。`;
        statusEl.classList.add('success');
    } else {
        statusEl.innerText = `上傳完成，但部分檔案失敗。成功: ${totalFilesCompleted}/${uploadTasks.length}`;
        statusEl.classList.add('error');
    }
}

// 處理上傳隊列，控制並行上傳數量
async function processUploadQueue() {
    // 初始化當前正在上傳的文件數量
    let activeUploads = 0;
    
    return new Promise(async (resolve) => {
        // 函數：開始下一個上傳任務
        const startNextUpload = async () => {
            // 獲取下一個待上傳的任務
            const nextTask = uploadTasks.find(task => task.status === 'pending');
            
            // 如果沒有待上傳的任務，則檢查是否都完成了
            if (!nextTask) {
                if (activeUploads === 0) {
                    resolve(); // 全部完成
                }
                return;
            }
            
            // 開始上傳
            nextTask.status = 'uploading';
            activeUploads++;
            
            try {
                // 執行檔案上傳
                await uploadFile(nextTask);
                nextTask.status = 'completed';
                totalFilesCompleted++;
                updateTotalProgress();
            } catch (error) {
                console.error(`上傳失敗 (${nextTask.file.name}):`, error);
                nextTask.status = 'failed';
                updateTaskUI(nextTask, `上傳失敗: ${error.message}`, 'error');
            } finally {
                activeUploads--;
                // 嘗試啟動下一個上傳
                startNextUpload();
            }
        };
        
        // 啟動初始的並行上傳
        for (let i = 0; i < maxConcurrentUploads; i++) {
            startNextUpload();
        }
    });
}

// 執行單個檔案的上傳
async function uploadFile(task) {
    const file = task.file;
    updateTaskUI(task, '準備上傳...', '');
    
    const chunkSize = 1024 * 1024; // 1MB 
    const totalChunks = Math.ceil(file.size / chunkSize);
    // 使用更安全的方式生成ID
    const uploadId = Date.now() + '_' + Math.random().toString(36).substring(2, 15) + '_' + file.name.replace(/[^a-zA-Z0-9._-]/g, '_');
    
    // 先取得已上傳的 chunks
    const checkForm = new FormData();
    checkForm.append('upload_id', uploadId);
    
    try {
        updateTaskUI(task, '檢查上傳狀態...', '');
        const checkResp = await fetch('/check_chunks', {
            method: 'POST',
            body: checkForm
        });
        
        if (!checkResp.ok) {
            throw new Error(`伺服器錯誤: ${checkResp.status} ${checkResp.statusText}`);
        }
        
        const checkResult = await checkResp.json();
        if (checkResult.error) {
            throw new Error(checkResult.error);
        }
        
        const uploadedChunks = new Set(checkResult.uploaded || []);
        updateTaskUI(task, `開始上傳，共 ${totalChunks} 個分塊...`, '');

        // 改用循環重試機制
        let maxRetries = 3;
        
        for (let i = 0; i < totalChunks; i++) {
            if (uploadedChunks.has(i)) {
                updateTaskProgress(task, (i + 1) / totalChunks * 100);
                continue; // 已上傳
            }

            let retries = 0;
            let success = false;
            
            while (retries < maxRetries && !success) {
                try {
                    updateTaskUI(task, `上傳分塊 ${i+1}/${totalChunks}...`, '');
                    const chunk = file.slice(i * chunkSize, (i + 1) * chunkSize);
                    const formData = new FormData();
                    formData.append('upload_id', uploadId);
                    formData.append('chunk_index', i);
                    formData.append('total_chunks', totalChunks);
                    formData.append('filename', file.name);
                    formData.append('file', chunk);

                    const response = await fetch('/upload_chunk', {
                        method: 'POST',
                        body: formData
                    });
                    
                    if (!response.ok) {
                        const errorText = await response.text();
                        throw new Error(`伺服器錯誤: ${response.status} - ${errorText || response.statusText}`);
                    }

                    const result = await response.json();
                    if (result.error) {
                        throw new Error(result.error);
                    }
                    
                    success = true;
                    updateTaskProgress(task, (i + 1) / totalChunks * 100);
                } catch (error) {
                    retries++;
                    console.error(`上傳分塊 ${i} 失敗，重試 (${retries}/${maxRetries}): ${error.message}`);
                    updateTaskUI(task, `上傳分塊 ${i+1} 失敗，重試... (${retries}/${maxRetries})`, 'error');
                    // 等待一段時間再重試
                    await new Promise(resolve => setTimeout(resolve, 1000));
                }
            }
            
            if (!success) {
                throw new Error(`上傳分塊 ${i+1} 失敗，已達最大重試次數`);
            }
        }
        
        // 所有分塊上傳完成後，檢查檔案合併狀態
        updateTaskUI(task, '🎉 上傳完成！處理檔案中...', '');
        
        // 發送請求確認檔案處理狀態
        const finalizeForm = new FormData();
        finalizeForm.append('upload_id', uploadId);
        finalizeForm.append('filename', file.name);
        finalizeForm.append('total_chunks', totalChunks);
        
        const finalizeResp = await fetch('/finalize_upload', {
            method: 'POST',
            body: finalizeForm
        });
        
        if (!finalizeResp.ok) {
            throw new Error(`伺服器處理檔案錯誤: ${finalizeResp.status} ${finalizeResp.statusText}`);
        }
        
        const finalizeResult = await finalizeResp.json();
        if (finalizeResult.error) {
            throw new Error(finalizeResult.error);
        }
        
        // 更新最終狀態
        updateTaskUI(task, '✅ 檔案上傳並處理完成！', 'success');
        
        // 可以顯示檔案的下載連結或其他相關資訊
        if (finalizeResult.file_path) {
            const filePathEl = document.createElement('div');
            filePathEl.innerHTML = `<a href="${escapeHtml(finalizeResult.file_path)}" target="_blank">${escapeHtml(finalizeResult.filename)}</a>`;
            task.fileElement.querySelector('.file-status').appendChild(filePathEl);
        }
        
        return finalizeResult;
    } catch (error) {
        console.error('上傳錯誤:', error);
        throw error;
    }
}

// 更新任務UI
function updateTaskUI(task, message, statusClass) {
    const statusEl = task.fileElement.querySelector('.file-status');
    statusEl.innerText = message;
    
    if (statusClass) {
        statusEl.className = 'file-status ' + statusClass;
    }
}

// 更新單個文件的進度
function updateTaskProgress(task, percent) {
    const progressFill = task.fileElement.querySelector('.file-progress-fill');
    progressFill.style.width = percent + '%';
}

// 將文件添加到UI列表
function addFileToList(file, statusText) {
    const fileListEl = document.getElementById('fileList');
    const fileItem = document.createElement('div');
    fileItem.className = 'file-item';
    
    // 計算合適的文件大小顯示
    const sizeDisplay = formatFileSize(file.size);
    
    fileItem.innerHTML = `
        <div class="file-name">${escapeHtml(file.name)} (${sizeDisplay})</div>
        <div class="file-status">${statusText}</div>
        <div class="file-progress">
            <div class="file-progress-fill"></div>
        </div>
    `;
    
    fileListEl.appendChild(fileItem);
    return fileItem;
}

// 重置總進度條
function resetProgressBar() {
    const bar = document.getElementById('progressBarFill');
    bar.style.width = '0%';
    bar.textContent = '0%';
}

// 更新總進度條
function updateTotalProgress() {
    const totalFiles = uploadTasks.length;
    if (totalFiles === 0) return;
    
    const completedFiles = totalFilesCompleted;
    const percent = Math.round((completedFiles / totalFiles) * 100);
    
    const bar = document.getElementById('progressBarFill');
    bar.style.width = percent + '%';
    bar.textContent = percent + '%';
}

// 檢查檔案類型是否允許
function isFileTypeAllowed(filename) {
    // 定義允許的副檔名列表 (與後端相同)
    const ALLOWED_EXTENSIONS = [
        // 文檔
        'pdf', 'doc', 'docx', 'txt', 'rtf', 'odt', 'xls', 'xlsx', 'ppt', 'pptx',
        // 圖片
        'jpg', 'jpeg', 'png', 'gif', 'bmp', 'svg', 'webp',
        // 音頻
        'mp3', 'wav', 'ogg', 'flac',
        // 視頻
        'mp4', 'avi', 'mov', 'mkv', 'webm',
        // 壓縮檔
        'zip', 'rar', '7z', 'tar', 'gz', 'tgz', 'bz2', 'xz',
        // 其他類型
        'csv', 'json', 'xml', 'html', 'css', 'js', 'txt', 'log', 'md', 'yaml', 'yml'
    ];
    
    // 取得副檔名並檢查
    const extension = filename.split('.').pop().toLowerCase();
    return ALLOWED_EXTENSIONS.includes(extension);
}

// 格式化文件大小顯示
function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    
    return parseFloat((bytes / Math.pow(1024, i)).toFixed(2)) + ' ' + sizes[i];
}

// 對字符串進行HTML編碼以防止XSS
function escapeHtml(str) {
    return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

// 頁面加載後添加事件監聽器
document.addEventListener('DOMContentLoaded', function() {
    const uploadButton = document.getElementById('uploadButton');
    if (uploadButton) {
        uploadButton.addEventListener('click', function() {
            startUpload();
        });
    }
    
    // 監聽檔案選擇變化，顯示檔案列表
    const fileInput = document.getElementById('fileInput');
    if (fileInput) {
        fileInput.addEventListener('change', function() {
            const fileListEl = document.getElementById('fileList');
            fileListEl.innerHTML = '';
            
            if (this.files.length > 0) {
                for (let i = 0; i < this.files.length; i++) {
                    const file = this.files[i];
                    addFileToList(file, '等待上傳...');
                }
            }
        });
    }
});
