# 開鍋抽好禮｜門市平板抽獎小工具

這是一個讓客人直接在平板上抽獎的小網頁：畫面上有四鍋可以選，動畫是互動效果，真正抽到什麼則由後端按照設定的獎項、機率與剩餘數量決定。

我想做的是一個**店員容易操作、抽獎紀錄也能留下來**的活動工具，而不是只有漂亮動畫的抽獎畫面。

## 已有功能

- 四格開鍋互動、動畫與抽獎結果。
- 獎品名稱、機率、庫存及未中獎項目設定。
- 庫存不足時調整可抽中的獎項、記錄每次抽獎。
- SQLite 本機保存與 CSV 匯出；亦可使用另行設定的 libSQL／Turso 連線。
- 管理後台 PIN 驗證：**沒有設定管理 PIN 時，管理頁會拒絕進入**，不再使用寫死的預設 PIN。

## 本機執行

```powershell
python -m pip install -r requirements.txt
# 請自行生成足夠長、且只存放於本機環境的管理 PIN
$env:LOTTERY_ADMIN_PIN = "請替換成至少12字元且不易猜出的隨機密碼"
streamlit run app.py
```

Windows 也可使用 `start_yihop_lottery.bat`，但如果需要管理後台，務必**先**在啟動 CMD 的環境設定 `LOTTERY_ADMIN_PIN`（或透過 Streamlit Secrets 設定）。程式會拒絕過短、過於簡單的 PIN。

顧客頁：`http://localhost:8501`；管理頁：`http://localhost:8501/?admin=1`，需要輸入管理 PIN 才能進入。

## 目前仍有的限制

**這是本機／受信任環境的活動原型，不是已完成公開網路驗收的正式服務。** PIN 是應用程式層的基本保護，尚未加入網路層存取控制、跨工作階段登入失敗限流、正式身分驗證與完整權限稽核。不要直接開放給不受信任的外網使用。

本機抽獎資料預設存於 `lottery.db`，不要提交 Git，請另外備份。Streamlit Community Cloud 的本機磁碟不是可靠的永久資料儲存方式。

## 可以聊的實作細節

抽獎的機率與庫存如何一起計算、重新整理時怎麼避免狀態遺失、要如何保留完整紀錄，以及從活動原型走到正式營運時還需要補哪些權限和資料庫設計。

[其他面試作品](https://github.com/garyai8146-jpg/Yihop_LineBOT/blob/main/PORTFOLIO.md)
