# WaterAI Embedded TTS

WaterAI TTS khong dung voice cua Windows/browser. Frontend goi `/api/tts`, backend doc manifest trong `tts/voices/manifest.json` va chay Piper cuc bo.

Can dat file:

- `tts/bin/piper.exe`
- `tts/voices/<language>/<style>.onnx`
- `tts/voices/<language>/<style>.onnx.json`

Vi du:

- `tts/voices/vi-VN/female-natural.onnx`
- `tts/voices/vi-VN/female-natural.onnx.json`

Kiem tra nhanh tren web bang popup chatbot settings. API `/api/tts/status` se bao engine/model nao dang thieu.
