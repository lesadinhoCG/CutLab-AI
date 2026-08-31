@echo off
title AI Shorts Studio

cd /d D:\CutLab-AI

call venv\Scripts\activate.bat

python -m streamlit run app.py

pause

