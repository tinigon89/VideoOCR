# VideoOCR

Quét cả thư mục video, nhận dạng giọng nói trong audio và xuất ra file phụ đề `.srt`
nằm cạnh từng video. Chạy hoàn toàn trên máy, không gửi gì lên mạng, không tốn phí API.

Tối ưu sẵn cho tiếng Trung phổ thông (普通话) và tiếng Anh.

---

## Cài đặt

Chạy **`setup.bat`** một lần. Script sẽ tự làm hết:

1. Kiểm tra Python (cần từ 3.9 trở lên)
2. Tạo môi trường ảo `.venv`
3. Cài faster-whisper cùng các thư viện CUDA
4. Tải ffmpeg về thư mục `bin\` nếu máy chưa có
5. Kiểm tra card đồ hoạ và driver

Lần cài đầu tải khoảng 1–2 GB nên hơi lâu. Nếu máy chưa có Python, tải tại
[python.org](https://www.python.org/downloads/) và **nhớ tích ô "Add python.exe to PATH"**.

### Chép sang máy khác

Thư mục `.venv` gắn chặt với đường dẫn và máy đã tạo ra nó, chép sang máy khác là
hỏng. Khi mang app sang máy mới, **hãy xoá `.venv` và `bin` rồi chạy lại `setup.bat`**
trên máy đó.

## Sử dụng

Chạy **`run.bat`** để mở cửa sổ. Chọn thư mục chứa video, bấm **Bắt đầu**.

Lần chạy đầu tiên còn phải tải model Whisper về (`large-v3` khoảng 3 GB), lưu vào
cache của máy nên những lần sau vào thẳng luôn.

### Bảng danh sách video

Ngay khi chọn thư mục, app quét và liệt kê từng video kèm **dung lượng** và **tình
trạng phụ đề**. Dòng tổng phía trên cho biết số video, tổng dung lượng, bao nhiêu
file đã có SRT và bao nhiêu còn thiếu.

Tích **"Chỉ hiện file chưa có SRT"** để ẩn bớt những file đã xong — tiện khi thư
mục có hàng trăm video làm dở.

Trong lúc chạy, cột trạng thái đổi theo thời gian thực:

| Trạng thái | Nghĩa |
|---|---|
| Chưa có SRT | Chưa có phụ đề, sẽ được xử lý |
| Đã có SRT | Đã có phụ đề, sẽ bỏ qua (trừ khi bật ghi đè) |
| Đang xử lý... | Video đang được nhận dạng |
| Xong · N khối | Đã xuất SRT với N khối phụ đề |
| Lỗi | Không xử lý được, xem lý do ở nhật ký |

Nút **Quét lại** làm mới danh sách nếu bạn vừa thêm hay xoá file bên ngoài.

### Các tuỳ chọn

| Mục | Ý nghĩa |
|---|---|
| **Ngôn ngữ** | Ngôn ngữ nói trong video. Chọn cố định chính xác hơn để máy tự đoán. |
| **Model** | `large-v3` chính xác nhất. `medium`/`small` nhanh hơn nhưng kém hơn rõ rệt với tiếng Trung. `distil-large-v3` nhanh gấp đôi nhưng **chỉ hiểu tiếng Anh**. |
| **Kiểu chữ Trung** | Ép kết quả về giản thể hoặc phồn thể. Whisper trả về lẫn lộn cả hai nên bước này khá cần. |
| **Compute type** | `float16` cho GPU. Nếu báo hết VRAM thì đổi sang `int8_float16`. |
| **Batch size** | Càng lớn càng nhanh nhưng càng tốn VRAM. 8 là vừa cho card 11 GB. |
| **Lọc khoảng lặng (VAD)** | Bỏ qua đoạn không có tiếng nói. Nên để bật. |
| **Lọc câu rác** | Xoá những câu quảng cáo Whisper hay bịa ra ở đoạn im lặng. Nên để bật. |
| **Ghi đè file .srt đã có** | Mặc định tắt, tức là video nào đã có phụ đề thì bỏ qua. Nhờ vậy chạy lại giữa chừng không phải làm lại từ đầu. |
| **Thư mục model** | Nơi tải và lưu model. Để trống là dùng `models\` cạnh app. |

### Model lưu ở đâu

Mặc định model nằm trong **`models\` ngay cạnh `run.bat`**, nên chép cả thư mục app
sang máy khác là dùng được luôn, không phải tải lại 3 GB.

`large-v3` nặng khoảng **3,1 GB**, tải một lần rồi thôi.

Nếu máy đã có sẵn model Whisper do phần mềm khác tải về, trỏ ô *Thư mục model* vào
cache chung của HuggingFace để dùng lại thay vì tải trùng:

```
C:\Users\<tên>\.cache\huggingface\hub
```

## Chạy bằng dòng lệnh

```bat
videoocr.bat "D:\Phim"                                  REM dùng lại lựa chọn lần trước
videoocr.bat "D:\Phim" --language zh --chinese s        REM tiếng Trung, ra giản thể
videoocr.bat "D:\Phim" --language en --model distil-large-v3
videoocr.bat "D:\Phim" --auto-language --overwrite
videoocr.bat "D:\Phim" --compute-type int8_float16 --batch-size 4
```

`videoocr.bat --help` để xem toàn bộ tham số.

---

## Cấu hình máy và tốc độ

Thiết kế nhắm tới máy **i5 thế hệ 10, RAM 32 GB, RTX 2080 Ti 11 GB**.

`large-v3` ở `float16` chiếm khoảng **4,5–5 GB VRAM**, thừa chỗ trong 11 GB. Tốc độ
thực tế nhanh hơn thời lượng video khoảng **5–8 lần**, tức video 1 tiếng mất chừng
8–12 phút.

Cần **driver NVIDIA từ 525 trở lên** để dùng CUDA 12. Driver cũ hơn thì app vẫn
chạy nhưng phải quay về CPU và chậm hơn rất nhiều — lúc đó nó sẽ báo rõ trong nhật ký.

## Gặp sự cố

**Báo hết VRAM (out of memory).** Đổi Compute type sang `int8_float16` để model tụt
xuống còn khoảng 2,5 GB, hoặc giảm Batch size xuống 4, hoặc đóng bớt game/phần mềm
dựng phim đang chiếm card.

**Chạy rất chậm, nhật ký ghi `cpu`.** Card không được nhận. Kiểm tra `nvidia-smi`
chạy được không, và cập nhật driver lên bản mới.

**Phụ đề tiếng Trung ra phồn thể trong khi muốn giản thể.** Đặt Kiểu chữ Trung là
*Giản thể*. Phần chuyển đổi chạy sau khi nhận dạng nên chắc chắn đồng nhất cả file.

**Phụ đề có những câu lạ kiểu "请不吝点赞订阅转发打赏".** Đó là câu rác Whisper học từ
phụ đề YouTube. Bật *Lọc câu rác* và *Lọc khoảng lặng*.

**Một video bị lỗi.** Loạt vẫn chạy tiếp, video lỗi được ghi vào nhật ký và bỏ qua.
Chạy lại lần nữa thì chỉ những video còn thiếu phụ đề mới được xử lý.

---

## Cấu trúc mã nguồn

```
videoocr/
  config.py       Cấu hình, danh sách ngôn ngữ/model, lưu lựa chọn người dùng
  scanner.py      Quét thư mục, quyết định video nào cần làm
  audio.py        Gọi ffmpeg tách audio ra WAV 16kHz mono
  gpu.py          Dò card, driver, nạp DLL CUDA cài qua pip
  transcriber.py  Bọc faster-whisper, nạp model một lần cho cả loạt
  subtitle.py     Cắt dòng (riêng cho CJK và Latin), dựng nội dung SRT
  cleaner.py      Lọc câu rác và khối lặp
  chinese.py      Chuyển giản thể <-> phồn thể bằng OpenCC
  pipeline.py     Điều phối toàn bộ, phát sự kiện tiến trình
  gui.py          Cửa sổ tkinter
  cli.py          Giao diện dòng lệnh
```

GUI và CLI dùng chung `pipeline.run()`, nên sửa logic ở một nơi là cả hai cùng đổi.

## Chạy test

```bat
.venv\Scripts\python.exe -m pytest tests -q
```

Toàn bộ test chạy được mà không cần GPU và không cần ffmpeg — phần nhận dạng
được thay bằng bản giả.
