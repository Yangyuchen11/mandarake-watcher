@echo off
chcp 65001 >nul
title Mandarake 【ドール】 商品爬虫与自动下单系统

echo ========================================================================
echo        Mandarake 【ドール】 专区商品爬虫与自动下单控制中心
echo ========================================================================
echo 正在启动 Mandarake 爬虫与下单引擎 (端口: 5005)...
echo 控制台地址: http://127.0.0.1:5005
echo.

python main.py --port 5005

if errorlevel 1 (
    echo.
    echo 程序发生异常退出，请检查上方日志报错信息。
    pause
)
