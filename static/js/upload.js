async function startUpload() {
    const fileInput = document.getElementById('fileInput');
    const statusEl = document.getElementById('status');
    const captchaInput = document.getElementById('captchaInput');
    
    // 驗證碼驗證
    const correctCaptcha = '54552253';
    if (captchaInput.value !== correctCaptcha) {
        statusEl.innerText = '錯誤：驗證碼不正確';
        statusEl.classList.add('error');
        return;
    }
    
    const file = fileInput.files[0];
    if (!file) {
        statusEl.innerText = '請選擇檔案';
        return;
    }
    
    // 限制檔案大小 (300GB)
    const maxSize = 300 * 1024 * 1024 * 1024;
    if (file.size > maxSize) {
        statusEl.innerText = '檔案大小超過限制 (300GB)';
        return;
    }
    
    statusEl.innerText = '準備上傳...';
    statusEl.classList.remove('error');
    const chunkSize = 1024 * 1024; // 1MB
    const totalChunks = Math.ceil(file.size / chunkSize);
    // 使用更安全的方式生成ID
    const uploadId = Date.now() + '_' + Math.random().toString(36).substring(2, 15) + '_' + file.name.replace(/[^a-zA-Z0-9._-]/g, '_');
    const progressBar = document.getElementById('progressBarFill');

    // 重置進度條
    progressBar.style.width = '0%';
    progressBar.textContent = '0%';
    
    // 先取得已上傳的 chunks
    const checkForm = new FormData();
    checkForm.append('upload_id', uploadId);
    
    try {
        statusEl.innerText = '檢查上傳狀態...';
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
        statusEl.innerText = `開始上傳，共 ${totalChunks} 個分塊...`;

        // 改用循環重試機制
        let maxRetries = 3;
        
        for (let i = 0; i < totalChunks; i++) {
            if (uploadedChunks.has(i)) {
                updateProgress(i + 1, totalChunks);
                continue; // 已上傳
            }

            let retries = 0;
            let success = false;
            
            while (retries < maxRetries && !success) {
                try {
                    statusEl.innerText = `上傳分塊 ${i+1}/${totalChunks}...`;
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
                    updateProgress(i + 1, totalChunks);
                } catch (error) {
                    retries++;
                    console.error(`上傳分塊 ${i} 失敗，重試 (${retries}/${maxRetries}): ${error.message}`);
                    statusEl.innerText = `上傳分塊 ${i+1} 失敗，重試... (${retries}/${maxRetries})`;
                    // 等待一段時間再重試
                    await new Promise(resolve => setTimeout(resolve, 1000));
                }
            }
            
            if (!success) {
                throw new Error(`上傳分塊 ${i+1} 失敗，已達最大重試次數`);
            }
        }
        
        // 所有分塊上傳完成後，檢查檔案合併狀態
        statusEl.innerText = '🎉 上傳完成！處理檔案中...';
        
        // 發送請求確認檔案處理狀態
        const finalizeForm = new FormData();
        finalizeForm.append('upload_id', uploadId);
        finalizeForm.append('filename', file.name);
        finalizeForm.append('total_chunks', totalChunks);
        
        try {
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
            statusEl.innerText = '✅ 檔案上傳並處理完成！';
            statusEl.classList.add('success');
            // 可以顯示檔案的下載連結或其他相關資訊
            if (finalizeResult.file_path) {
                const filePathEl = document.createElement('div');
                filePathEl.innerHTML = `檔案路徑: <span class="file-path">${escapeHtml(finalizeResult.file_path)}</span>`;
                statusEl.appendChild(filePathEl);
            }
        } catch (finalizeError) {
            console.error('檔案處理錯誤:', finalizeError);
            statusEl.innerText = `檔案已上傳，但處理過程發生錯誤: ${finalizeError.message}`;
            statusEl.classList.add('warning');
        }
    } catch (error) {
        console.error('上傳錯誤:', error);
        statusEl.innerText = '錯誤: ' + error.message;
        statusEl.classList.add('error');
    }
}

function updateProgress(completed, total) {
    const percent = Math.round((completed / total) * 100);
    const bar = document.getElementById('progressBarFill');
    
    // 安全處理DOM內容
    bar.style.width = percent + '%';
    bar.textContent = percent + '%';
    if (percent === 100) {
        document.getElementById('status').innerText = '🎉 上傳完成';
    }
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
});
