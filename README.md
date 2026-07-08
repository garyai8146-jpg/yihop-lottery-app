# 藝鍋物｜開鍋抽好禮

這是一個適合櫃檯平板使用的 Streamlit 抽獎網頁。客人可從四格火鍋海報中選一格；火鍋只是互動選擇，真正結果由 Python 後端依照後台設定的機率與庫存即時決定。

## 已完成

- 藝鍋物風格：四格滿版火鍋抽抽樂海報、蒸氣與開鍋動畫
- 固定四格抽獎頁
- 好運紅鍋、黃金旺鍋、招財辣鍋、幸福暖鍋
- 每位客人可抽次數設定
- 獎品名稱、圖示、機率、數量、是否中獎與顯示文字設定
- 數量 0 代表不限量
- 庫存用完後自動停止抽出該獎項
- 下一位客人、撤銷最後一抽、暫停活動
- 抽獎紀錄與 CSV 匯出
- SQLite 持久化與交易鎖，重新整理不會重複發獎
- 免 PIN 管理後台

## Windows 一鍵啟動

1. 將 `app.py`、`requirements.txt`、`start_yihop_lottery.bat` 放在同一個資料夾。
2. 雙擊 `start_yihop_lottery.bat`。
3. 首次執行會自動建立 `.venv` 並安裝套件。
4. 瀏覽器開啟 `http://localhost:8501`。

管理後台：`http://localhost:8501/?admin=1`

## 同一個 Wi-Fi 讓平板開啟

啟動電腦與平板連到同一個 Wi-Fi，平板瀏覽器輸入：

```text
http://電腦的區域網路IP:8501
```

例如：

```text
http://192.168.1.35:8501
```

Windows 可在命令提示字元執行 `ipconfig`，查看目前網卡的 IPv4 位址。第一次連線時，Windows 防火牆可能詢問是否允許 Python／Streamlit，請允許私人網路。

## 後台機率規則

- 所有「啟用」獎項的機率合計必須為 100%。
- 獎品數量填 `0` 代表不限量。
- 有限量獎品抽完後，原本屬於它的機率會落到可用的「未中獎」項目，不會提高其他獎品的機率。
- 建議保留一個不限量的「好運正在熬煮中」獎項，避免全部有限庫存用完後無法抽獎。

## 資料檔案

系統首次啟動後，會在程式資料夾建立：

```text
lottery.db
```

請定期備份此檔案。刪除它會使系統回到初始資料。

也可用環境變數指定資料庫位置：

```text
LOTTERY_DB_PATH=D:\LotteryData\lottery.db
```

## 雲端部署提醒

本專案可部署到 Streamlit Community Cloud：

1. 將專案推到 GitHub repo。
2. 到 <https://share.streamlit.io/> 選擇該 repo。
3. Main file path 填 `app.py`。
4. Python 版本由 `runtime.txt` 指定為 `python-3.12`。
5. 部署完成後，管理後台網址為：

```text
https://你的streamlit網址/?admin=1
```

SQLite 適合先在本機、區域網路，或測試版雲端使用。Streamlit Community Cloud 不保證本機檔案永久保存，重啟或重新部署後 `lottery.db` 可能回到初始資料；正式營運時應改用 PostgreSQL、Supabase 等雲端資料庫，或改部署到有持久磁碟的平台。
