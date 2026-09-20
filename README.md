# VideoOCR

Quét cả thư mục video, nhận dạng giọng nói trong audio và xuất ra file phụ đề `.srt`
nằm cạnh từng video. Phần nhận dạng chạy hoàn toàn trên máy, không gửi gì lên mạng
và không tốn phí.

Tối ưu sẵn cho tiếng Trung phổ thông (普通话) và tiếng Anh. Có thêm phần dịch sang
tiếng Việt bằng Google Gemini — phần này thì có gọi ra mạng, và chỉ chạy khi bạn bật.

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

Bảng có **hai cột trạng thái tách riêng** — phụ đề gốc và bản dịch không gộp chung:

| Cột **Phụ đề** | Nghĩa |
|---|---|
| Chưa có | Chưa có phụ đề, sẽ được xử lý |
| Đã có | Đã có phụ đề, sẽ bỏ qua (trừ khi bật ghi đè) |
| Đang xử lý... | Đang nhận dạng |
| Xong · N khối | Vừa xuất SRT với N khối phụ đề |
| Lỗi | Không xử lý được, xem lý do ở nhật ký |

| Cột **Bản dịch** | Nghĩa |
|---|---|
| Chưa có | Chưa có file .vi.srt |
| Đã có | Đã có bản tiếng Việt từ trước |
| Đang dịch... | Đang gửi sang Gemini |
| Xong | Vừa dịch xong |
| Lỗi | Dịch không thành công; bản nguyên ngữ vẫn còn nguyên |

Nút **Quét lại** làm mới danh sách nếu bạn vừa thêm hay xoá file bên ngoài.

### Màn hình nhỏ

Hai khung *Tuỳ chọn nhận dạng* và *Dịch tiếng Việt* **gập lại được** — bấm vào tiêu
đề để đóng/mở, app nhớ trạng thái cho lần sau. Ranh giới giữa bảng danh sách và
nhật ký **kéo được bằng chuột**.

Nếu cửa sổ quá thấp để chứa hết, app tự gập bớt khung để bảng danh sách luôn có chỗ
và nút Bắt đầu không bị đẩy ra ngoài. Nó chỉ gập chứ không bao giờ tự mở, và không
ghi đè lựa chọn bạn đã lưu.

### Các tuỳ chọn

| Mục | Ý nghĩa |
|---|---|
| **Ngôn ngữ** | Ngôn ngữ nói trong video. Chọn cố định chính xác hơn để máy tự đoán. |
| **Model** | `large-v3` chính xác nhất. `medium`/`small` nhanh hơn nhưng kém hơn rõ rệt với tiếng Trung. `distil-large-v3` nhanh gấp đôi nhưng **chỉ hiểu tiếng Anh**. Ngay dưới có dòng gợi ý model hợp với ngôn ngữ đang chọn, chuyển màu cam khi bạn chọn model không phù hợp. |
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

## Dịch sang tiếng Việt bằng Gemini

Tích **"Dịch tự động sau khi nhận dạng xong"**, dán API key rồi chạy như bình thường.
Mỗi video sẽ cho ra hai file:

```
EP01.mp4
EP01.srt        <- nguyên ngữ (tiếng Trung)
EP01.vi.srt     <- tiếng Việt
```

Bản gốc luôn được giữ lại. Nếu dịch hỏng giữa chừng, bạn vẫn còn phụ đề nguyên ngữ
và chỉ cần bấm **"Dịch các SRT đã có"** để làm lại riêng phần dịch — không phải
nhận dạng lại từ đầu.

### Lấy API key

Tạo miễn phí tại [aistudio.google.com/apikey](https://aistudio.google.com/apikey).

### Nhiều key và cơ chế xoay vòng

Dán **mỗi dòng một key**. Khi một key báo hết hạn mức, app tự chuyển sang key kế tiếp
và cho key đó nghỉ một phút rồi mới dùng lại. Key sai hoặc bị thu hồi thì loại hẳn
khỏi vòng quay ngay lần đầu.

> **Quan trọng:** Google tính hạn mức theo **project**, không theo từng API key.
> Nhiều key tạo trong cùng một project vẫn dùng chung một quota, xoay vòng sẽ không
> tăng thêm được gì. Muốn có tác dụng thật, mỗi key phải thuộc một project khác nhau
> (hoặc một tài khoản Google khác nhau).

Nút **"Kiểm tra key"** hỏi thẳng Google xem từng key còn sống không.

Key hiển thị ở dạng che (`AIza••••••••1R1s`), bấm **"Hiện key"** mới thấy đầy đủ.
Nhờ vậy chụp màn hình gửi cho người khác không bị lộ key.

### Chọn model

Danh sách trong dropdown ban đầu chỉ là gợi ý — Google thêm bớt model liên tục nên
danh sách cố định luôn lạc hậu. Bấm **"Lấy danh sách"** để hỏi thẳng API xem tài
khoản của bạn dùng được những model nào.

App tự lọc bỏ những model không dịch chữ được (nhúng vector, sinh ảnh, sinh video,
đọc giọng nói) và xếp model ổn định lên trước bản preview. Nếu model đang chọn
không có trong danh sách thật, app tự đổi sang model đầu tiên và báo trong nhật ký.

### Dặn cách xưng hô

Ô **Hướng dẫn dịch** là chỗ đáng bỏ công nhất. Tiếng Trung và tiếng Anh không phân
biệt vai vế, nên máy dịch phải tự đoán xưng hô — và đoán sai thì cả bộ phim nghe
lạc giọng. Dặn trước một câu là khác hẳn:

```
Bối cảnh cổ trang. Dùng lối xưng hô cổ: 'ta - ngươi', 'tại hạ', 'các hạ',
'muội', 'huynh'. Giữ nguyên các chức danh như hoàng thượng, công tử, tiểu thư.
```

Dropdown bên phải có sẵn vài mẫu: phim hiện đại (anh/em), phim cổ trang (ta/ngươi),
phim gia đình, tài liệu thuyết minh. Chọn mẫu rồi sửa lại cho hợp phim của bạn.

Hướng dẫn này được đánh dấu là **ưu tiên cao** trong lời nhắc, nên Gemini sẽ theo nó
thay vì thói quen dịch mặc định. Các quy tắc giữ nguyên số dòng và mốc thời gian
vẫn được áp dụng đầy đủ.

### Mốc thời gian có bị lệch không

Không. App gửi từng lô phụ đề kèm số thứ tự và bắt Gemini trả về JSON đúng theo số
thứ tự đó, nên số khối và mốc thời gian giữ nguyên tuyệt đối. Dòng nào Gemini bỏ sót
thì giữ nguyên bản gốc chứ không làm xô lệch những dòng còn lại.

### Nơi lưu API key

Key được lưu cùng các thiết lập khác trong `%APPDATA%\VideoOCR\settings.json`, dạng
văn bản thường, không mã hoá. File nằm ngoài thư mục app nên không lọt vào git, nhưng
nếu máy dùng chung với người khác thì bạn nên cân nhắc.

Nhật ký chỉ hiện 4 ký tự cuối của key (`key #1 (...aB3k)`), không bao giờ ghi cả key
ra màn hình.

## Chạy bằng dòng lệnh

```bat
videoocr.bat "D:\Phim"                                  REM dùng lại lựa chọn lần trước
videoocr.bat "D:\Phim" --language zh --chinese s        REM tiếng Trung, ra giản thể
videoocr.bat "D:\Phim" --language en --model distil-large-v3
videoocr.bat "D:\Phim" --auto-language --overwrite
videoocr.bat "D:\Phim" --compute-type int8_float16 --batch-size 4

REM Dịch tiếng Việt, nhiều key thì lặp lại --gemini-key
videoocr.bat "D:\Phim" --translate --gemini-key AIza... --gemini-key AIza...

REM Chỉ dịch các .srt đã có, không nhận dạng lại
videoocr.bat "D:\Phim" --translate-only --gemini-key AIza...
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

**Lỗi `WinError 32 - file đang được tiến trình khác sử dụng`.** App ghi phụ đề ra
file `.srt.part` rồi mới đổi tên thành `.srt`. Trên Windows, phần mềm diệt virus
quét file ngay khi nó vừa ghi xong và giữ khoá trong tích tắc — rơi đúng khoảnh
khắc đó thì bước đổi tên hỏng.

App tự thử lại nhiều nhịp trong khoảng 6 giây, và nếu vẫn không được thì ghi thẳng
vào file đích thay vì bỏ cuộc. Ngoài ra mỗi lần chạy nó còn tự tìm các file `.part`
còn sót từ lần trước và đổi tên lại giúp bạn, nên công nhận dạng không bị mất.

Nếu vẫn gặp thường xuyên, thêm thư mục video vào danh sách loại trừ của Windows
Security (Virus & threat protection → Manage settings → Exclusions) sẽ dứt điểm.

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
  translator.py   Gọi Gemini dịch tiếng Việt, xoay vòng API key
  gui.py          Cửa sổ tkinter
  cli.py          Giao diện dòng lệnh
```

GUI và CLI dùng chung `pipeline.run()`, nên sửa logic ở một nơi là cả hai cùng đổi.

## Chạy test

```bat
.venv\Scripts\python.exe -m pytest tests -q
```

Toàn bộ test chạy được mà không cần GPU, không cần ffmpeg và không gọi API thật —
phần nhận dạng và phần gọi Gemini đều được thay bằng bản giả, nên chạy test không
tốn hạn mức API.
