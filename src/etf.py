import akshare as ak
import pandas as pd

etf = ak.fund_etf_hist_em(
    symbol="510300",
    period="daily",
    start_date="20200101",
    end_date="20260101",
    adjust="qfq"
)

print(etf.head())