# 藝鍋物｜開鍋抽好禮（Streamlit 抽獎原型）

> 為門市平板設計的四格開鍋互動，抽獎結果由 Python 程式依獎項設定與庫存決定，並保留抽獎紀錄。

[GitHub 面試作品導覽](https://github.com/garyai8146-jpg/Yihop_LineBOT/blob/main/PORTFOLIO.md)

## 功能

- 四格火鍋互動介面與動畫。
- 獎項、機率、庫存、未中獎項目的後台設定。
- 庫存為 0 時的獎項分配處理、抽獎紀錄及 CSV 匯出。
- 透過 SQLite 保存資料；可選擇以外部 libSQL／Turso 設定運作，需另行驗證部署環境。

## 本機操作

```powershell
python -m pip install -r requirements.txt
streamlit run app.py
```

或 Windows 雙擊 `start_yihop_lottery.bat`。顧客操作頁預設為 `http://localhost:8501`；後台目前以 `?admin=1` 進入。

## 重要安全限制（面試前請閱讀）

**目前管理頁沒有強制執行身分驗證，不能將這個版本直接公開上網供真實活動使用。** 雖然程式初始化會保存 `admin_pin_hash`，但進入管理頁並未驗證 PIN；設定 `LOTTERY_ADMIN_PIN` 並不能補足這個缺口。

因此現階段僅適合**可信任的本機測試環境**展示，避免對不可信任的 Wi-Fi／網路開放連接埠。上線前需補上伺服器端授權、強制驗證、暴力嘗試限制，並使用受控資料庫與持久化備份。對外公開部署需完成獨立資安驗收。

## 資料持久化

本機預設建立 `lottery.db`。可透過 `LOTTERY_DB_PATH` 指定路徑。SQLite 檔案應排除 Git 並定期備份；Streamlit Community Cloud 的本機儲存空間不能視為永久可靠資料庫。

## 面試展示重點

可展示事件式抽獎 UI、有限獎品的機率分配規則、SQLite 狀態更新，以及如何把原型升級為有權限、稽核與交易保護的正式服務。不要把現有管理頁稱為已安全部署。
