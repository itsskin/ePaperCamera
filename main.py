import time

import wifi_manager
from web_app import run_server

CAMERA_PINS = dict(
    data_pins=[11, 9, 8, 10, 12, 18, 17, 16],
    pclk_pin=13,
    vsync_pin=6,
    href_pin=7,
    sda_pin=4,
    scl_pin=5,
    xclk_pin=15,
    xclk_freq=20000000,
    powerdown_pin=-1,
    reset_pin=-1,
)

CAMERA_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>ePaperCamera</title>
<style>body{font-family:sans-serif;max-width:840px;margin:20px auto;padding:0 16px;text-align:center}
img{width:100%;border:1px solid #ccc;margin-top:12px}
select{width:100%;padding:10px;font-size:16px;margin-top:12px}
input{width:100%;padding:10px;font-size:16px;margin-top:8px;box-sizing:border-box}
button{width:100%;padding:14px;font-size:18px;margin-top:12px}</style></head>
<body><h2>ePaperCamera</h2>
<img id="p" src="/dither">
<select id="delay">
<option value="0">Без задержки</option>
<option value="0.5" selected>Задержка 0.5 с</option>
<option value="1">Задержка 1 с</option>
<option value="2">Задержка 2 с</option>
<option value="3">Задержка 3 с</option>
<option value="5">Задержка 5 с</option>
<option value="10">Задержка 10 с</option>
</select>
<button onclick="shoot()">Сделать фото</button>
<p style="color:#888;font-size:14px">Кнопка BOOT на плате: короткое нажатие — снимок в выбранном режиме и с той же задержкой,
удержание 1.2 с — предыдущий кадр из истории на экран, 3 с — шаг вперёд.
Светодиод мигает, когда порог удержания пройден.</p>
<p id="s"></p>
<h3>На экране (400x300, обрезка по краям)</h3>
<img id="epaper" src="/epaper_preview">
<button onclick="changeMode()">Перерисовать экран</button>
<h3>Сохранённые снимки</h3>
<div style="display:flex;gap:8px;align-items:center;margin-top:12px">
<button onclick="step(-1)" style="width:64px;margin:0;padding:10px">&#9664;</button>
<select id="hist" onchange="showSaved()" style="flex:1;margin:0"></select>
<button onclick="step(1)" style="width:64px;margin:0;padding:10px">&#9654;</button>
</div>
<img id="saved">
<button onclick="pushSaved()">Вывести выбранный на экран</button>
<button onclick="deleteSaved()" style="background:#fee">Удалить выбранный</button>
<h3>Своё изображение</h3>
<input type="file" id="file" accept="image/*">
<button onclick="uploadPhoto()">Обработать и вывести на экран</button>
<h3>Обновление с GitHub</h3>
<p id="ota" style="color:#888;font-size:14px">Версия не проверена</p>
<button onclick="otaCheck()">Проверить обновления</button>
<button id="otaApply" onclick="otaApply()" style="display:none">Обновить и перезагрузить</button>
<h3>Домашняя сеть</h3>
<p style="color:#888;font-size:14px">Домашняя сеть: <b>{cur_ssid}</b>, адрес {cur_ip}<br>
Точка доступа <b>{ap_ssid}</b> поднята всегда — http://{cur_ap}/<br>
Камера работает и без домашней сети, через точку доступа.</p>
<form action="/wifi_save" method="get">
<input name="ssid" value="{cur_ssid}" placeholder="SSID домашней сети" required>
<input name="password" type="password" placeholder="Пароль домашней сети (пусто — не менять)">
<input name="ap_password" type="password" placeholder="Новый пароль точки доступа (8+ символов)">
<input name="push_token" placeholder="Токен заливки кода (пусто — не менять, минус — снять)">
<button type="submit">Сохранить и перезагрузить</button>
</form>
<p style="color:#888;font-size:14px">{security}</p>
<script>
function redraw(){
  document.getElementById('p').src = '/dither?t=' + Date.now();
}
function changeMode(){
  redrawEpaper();
  document.getElementById('s').textContent = 'Перерисовываю экран...';
  fetch('/render').then(r=>r.text()).then(t=>{
    document.getElementById('s').textContent = t;
  });
}
function redrawEpaper(){
  document.getElementById('epaper').src = '/epaper_preview?t=' + Date.now();
}
function loadList(fallbackIndex){
  fetch('/list').then(r=>r.json()).then(items=>{
    const sel = document.getElementById('hist');
    const was = sel.value;
    sel.innerHTML = '';
    items.forEach(it=>{
      const o = document.createElement('option');
      o.value = it.name;
      o.textContent = it.name;
      sel.appendChild(o);
    });
    if (!items.length) {
      document.getElementById('saved').removeAttribute('src');
      return;
    }
    const names = items.map(it=>it.name);
    let idx = names.indexOf(was);
    if (idx < 0) {
      // Показанного снимка в списке больше нет — его удалили. Встаём
      // рядом, а не в начало списка: иначе после удаления старого кадра
      // выбор улетает к самому свежему, и до места приходится листать
      // заново.
      idx = (fallbackIndex === undefined) ? 0 : fallbackIndex;
      if (idx < 0) idx = 0;
      if (idx > names.length - 1) idx = names.length - 1;
    }
    sel.selectedIndex = idx;
    showSaved();
  });
}
function step(d){
  // Список идёт от новых к старым, поэтому влево — к более свежим.
  const sel = document.getElementById('hist');
  if (!sel.options.length) return;
  let i = sel.selectedIndex + d;
  if (i < 0) i = 0;
  if (i >= sel.options.length) i = sel.options.length - 1;
  if (i !== sel.selectedIndex) { sel.selectedIndex = i; showSaved(); }
}
function showSaved(){
  const n = document.getElementById('hist').value;
  if (n) document.getElementById('saved').src = '/photo?name=' + n;
}
function pushSaved(){
  const n = document.getElementById('hist').value;
  if (!n) return;
  document.getElementById('s').textContent = 'Вывожу ' + n + ' на экран...';
  fetch('/show?name=' + n).then(r=>r.text()).then(t=>{
    document.getElementById('s').textContent = t;
  });
}
function deleteSaved(){
  const n = document.getElementById('hist').value;
  if (!n) return;
  if (!confirm('Удалить ' + n + '? Это навсегда.')) return;
  const prev = document.getElementById('hist').selectedIndex - 1;
  fetch('/delete?name=' + n).then(r=>r.text()).then(t=>{
    document.getElementById('s').textContent = t;
    // Показанный снимок только что исчез — подтягиваем список заново,
    // иначе в селекте осталось бы имя удалённого файла.
    loadList(prev);
  });
}
let lastSeq = -1, webShot = false;
function poll(){
  fetch('/status').then(r=>r.json()).then(st=>{
    if (lastSeq < 0) { lastSeq = st.seq; return; }
    if (st.seq === lastSeq) return;
    lastSeq = st.seq;
    // Кадр появился не из этой вкладки — значит нажали кнопку на плате.
    if (webShot) return;
    document.getElementById('s').textContent = 'Снято кнопкой на плате';
    redrawEpaper();
    redraw();
    setTimeout(loadList, 8000);
  }).catch(function(){});
}
setInterval(poll, 2000);
function otaCheck(){
  const el = document.getElementById('ota');
  el.textContent = 'Проверяю...';
  fetch('/ota_check').then(r=>r.json()).then(d=>{
    if (d.error) { el.textContent = 'Ошибка: ' + d.error; return; }
    el.textContent = 'На плате версия ' + d.current_version
      + ', в репозитории ' + d.available_version
      + (d.update_available ? ' — есть обновление' : ' — уже актуальна');
    document.getElementById('otaApply').style.display =
      d.update_available ? 'block' : 'none';
  }).catch(e=>{ el.textContent = 'Ошибка: ' + e; });
}
function otaApply(){
  const el = document.getElementById('ota');
  if (!confirm('Обновить код платы с GitHub и перезагрузиться?')) return;
  el.textContent = 'Качаю и проверяю файлы, это может занять минуту...';
  // Запрос держится всё время скачивания: сервер однопоточный, и
  // показать прогресс во время него всё равно нечем.
  fetch('/ota_apply').then(r=>r.text()).then(t=>{ el.textContent = t; })
    .catch(e=>{ el.textContent = 'Ошибка: ' + e; });
}
function uploadPhoto(){
  const f = document.getElementById('file').files[0];
  const st = document.getElementById('s');
  if (!f) { st.textContent = 'Файл не выбран'; return; }
  st.textContent = 'Готовлю ' + f.name + '...';
  const url = URL.createObjectURL(f);
  const img = new Image();
  img.onload = function(){
    // Обрезаем по краям до 4:3 и не сжимаем — то же правило, что для
    // кадра с камеры. Декодирование картинки делает браузер: на плате
    // декодера JPEG/PNG просто нет.
    const target = 400 / 300;
    let sw = img.width, sh = img.height, sx = 0, sy = 0;
    if (sw / sh > target) { const nw = Math.round(sh * target); sx = (sw - nw) >> 1; sw = nw; }
    else { const nh = Math.round(sw / target); sy = (sh - nh) >> 1; sh = nh; }
    const c = document.createElement('canvas');
    c.width = 400; c.height = 300;
    c.getContext('2d').drawImage(img, sx, sy, sw, sh, 0, 0, 400, 300);
    URL.revokeObjectURL(url);
    const d = c.getContext('2d').getImageData(0, 0, 400, 300).data;
    // BT.601 — стандартные коэффициенты яркости
    const gray = new Uint8Array(400 * 300);
    for (let i = 0, j = 0; j < gray.length; i += 4, j++) {
      gray[j] = (d[i] * 77 + d[i+1] * 150 + d[i+2] * 29) >> 8;
    }
    st.textContent = 'Отправляю на плату (117 КБ)...';
    fetch('/upload', {method: 'POST', body: gray})
      .then(r=>r.text()).then(t=>{
        st.textContent = t;
        setTimeout(loadList, 12000);
      }).catch(e=>{ st.textContent = 'Ошибка отправки: ' + e; });
  };
  img.onerror = function(){ URL.revokeObjectURL(url); st.textContent = 'Не удалось прочитать файл'; };
  img.src = url;
}
function shoot(){
  webShot = true;
  const st = document.getElementById('s');
  const d = parseInt(document.getElementById('delay').value, 10) || 0;
  // Отсчёт рисуем на странице, а ждёт сам сервер: задержка должна
  // работать и для кнопки на плате, где никакого браузера нет.
  let left = d;
  st.textContent = left > 0 ? 'Снимаю через ' + left + '...' : 'Снимаю...';
  const timer = setInterval(function(){
    left -= 1;
    st.textContent = left > 0 ? 'Снимаю через ' + left + '...' : 'Снимаю...';
    if (left <= 0) clearInterval(timer);
  }, 1000);
  fetch('/capture?delay=' + d).then(r=>r.text()).then(t=>{
    clearInterval(timer);
    document.getElementById('s').textContent = t;
    // Сервер однопоточный: два запроса подряд он отдаёт по очереди.
    // Сначала маленькое e-paper превью (~15КБ, доли секунды), большой
    // "Оригинал" (~308КБ, несколько секунд на Wi-Fi) — только после него,
    // чтобы на экране что-то появилось сразу, а не через всю передачу.
    const ep = document.getElementById('epaper');
    const p = document.getElementById('p');
    p.onload = p.onerror = function(){ p.onload = p.onerror = null;
      document.getElementById('s').textContent = t + ' — вывожу на экран...';
      // На панель и в историю — только когда в вебе всё уже показано:
      // обновление e-paper само по себе занимает секунды.
      fetch('/save').then(()=>{
        webShot = false;
        document.getElementById('s').textContent = t + ' — отправлено на экран';
        // Снимок пишется на flash в том же фоновом потоке, что и вывод
        // на панель, — список обновляем после него, а не сразу.
        setTimeout(loadList, 4000);
      });
    };
    ep.onload = ep.onerror = function(){ ep.onload = ep.onerror = null;
      document.getElementById('s').textContent = t + ' — загружаю полный кадр...';
      redraw(); };
    redrawEpaper();
  }).catch(e=>{clearInterval(timer); webShot = false; st.textContent = 'Ошибка: ' + e;});
}
loadList();
</script>
</body></html>"""


DISPLAY_WIDTH = 400
DISPLAY_HEIGHT = 300


# Разрешение съёмки. Пробовали подбирать динамически (опрос
# get_max_frame_size() + перебор всех пресетов FrameSize в поисках
# половины нативного разрешения) — HW-подтверждено, это и сломало ранее
# стабильную съёмку: перебор дёргает set_frame_size() два десятка раз
# подряд на живой камере, у которой буфер выделен под другой размер, и
# драйвер после этого выдаёт рваные кадры (повторяющийся полосатый
# паттерн) уже на ЛЮБОМ разрешении, включая VGA, который до перебора
# работал безупречно много раз подряд. Фиксированный VGA в конструкторе,
# без последующих set_frame_size — единственная конфигурация, которая
# HW-подтверждённо стабильна внутри реального приложения.
PHOTO_WIDTH = 640
PHOTO_HEIGHT = 480

# Через сколько простоя гасить камеру (она греется, пока инициализирована).
# Минута, а не 10с, как было: холодный старт сенсора это ~1.6с, и при
# пороге в 10 секунд камера успевала погаснуть между любыми двумя
# нажатиями кнопки — то есть эти 1.6с платились КАЖДЫЙ раз. За минуту
# серия снимков успевает пройти целиком на тёплом сенсоре, а долгий
# простой по-прежнему её гасит.
#
# Это прямой обмен нагрева и расхода батареи на скорость: пока камера
# инициализирована, сенсор при fb_count=1 непрерывно гоняет кадры и ест
# больше всех на плате. Если начнёт ощутимо греться — уменьшать здесь.
CAMERA_IDLE_MS = 60000

# Задержка между нажатием и кадром (автоспуск). Живёт на плате, а не в
# браузере: кнопкой BOOT снимают вообще без веба, и задержка там нужна
# та же самая.
DEFAULT_DELAY_MS = 500


# Красный светодиод платы. Нашёлся перебором свободных выводов: зелёный
# рядом с ним — индикатор питания, он висит на 3.3В напрямую и никакому
# выводу не подчиняется.
LED_PIN = 2
LED_ACTIVE_HIGH = True

# Светодиод просто загорается на время и гаснет сам, без моргания.
# Гашение по таймеру, а не сном в потоке съёмки: иначе на эти секунды
# встал бы и ответ веб-странице, и обработка следующего нажатия.
LED_SHOT_MS = 3000
LED_NAV_MS = 1000

# Кнопка BOOT: одно нажатие — снимок, двойное — предыдущий кадр из
# истории на экран, тройное — шаг вперёд.
#
# Кнопка: короткое нажатие — снимок, удержание — листание истории.
#
# Серии нажатий (двойное, тройное) пришлось убрать. GPIO0 — вывод схемы
# автосброса, на нём висит конденсатор: при отпускании вывод возвращается
# к питанию медленно, и на этом фронте вход даёт лишние срабатывания.
# Появляются они примерно через длительность нажатия после нажатия, то
# есть по времени неотличимы от настоящего второго щелчка — отсюда и
# срабатывание через раз, которое так и не удалось отфильтровать.
#
# Длительность удержания такой помехе не подвержена вовсе: она читается
# по уровню вывода, а не по фронтам.
BUTTON_DEBOUNCE_MS = 40
BUTTON_HOLD_PREV_MS = 1200
BUTTON_HOLD_NEXT_MS = 3000


# Зелёный светодиод на плате — индикатор питания, он подключён к линии
# 3V3 напрямую и никакому GPIO не подчиняется: погасить его программно
# нельзя (проверено — прогон всех свободных пинов ничего не дал).
RESET_CAUSES = {1: "POWERON", 2: "EXT", 3: "SOFT", 4: "WDT",
                5: "DEEPSLEEP", 6: "BROWNOUT"}


def valid_module_name(name):
    """Пускаем только имена модулей проекта в корне флеша.

    Заливка по сети — это, по сути, выполнение произвольного кода на
    плате, поэтому путь не должен приходить из запроса как есть. Ни
    подкаталогов, ни "..", ни чего-либо кроме .py: снимки, конфиг Wi-Fi и
    прошивку через эту дверь не тронуть."""
    if not name.endswith(".py") or len(name) > 32 or len(name) < 4:
        return False
    for ch in name:
        if not (ch.isalpha() or ch.isdigit() or ch in "_."):
            return False
    return ".." not in name


# Явных gc.collect() в обработчиках больше нет.
#
# Они стояли против фрагментации, но оказались куда дороже: полная сборка
# по многомегабайтной куче PSRAM идёт около двух секунд и ничему не
# уступает управление. Физическая кнопка опрашивается в том же потоке —
# то есть каждый такой вызов был окном, в которое нажатие не замечалось
# (HW-замерено: промежуток между опросами кнопки доходил до 2.4с).
# Собирать мусор MicroPython умеет сам, при нехватке памяти на выделение.
def run_camera_server(ip, ap_ip=""):
    from camera import Camera, PixelFormat, FrameSize
    from machine import Pin, Timer
    from bmp import gray4_to_bmp
    import dither
    import epaper
    import persist


    # Пробовали fb_count=2 + GrabMode.LATEST вместо ручного двойного
    # захвата — HW-подтверждено ХУЖЕ на этой связке (постоянные cam_hal
    # FB-OVF/FB-SIZE даже при прогреве). Остаёмся на fb_count=1 — это
    # именно то, что стабильно отработало предыдущую сессию тестов.
    #
    # PixelFormat.GRAYSCALE для ОСНОВНОГО потока, не YUV422: HW-
    # подтверждено (дорогой ценой) — YUV422 (2 байта/пиксель) рвёт кадры
    # (повторяющийся полосатый паттерн, реальная порча данных, не просто
    # консольные warning'и) внутри РЕАЛЬНОГО приложения (веб-сервер +
    # фоновый поток) даже на VGA, хотя в изолированных тестах то же самое
    # разрешение шло идеально чисто раз за разом. GRAYSCALE (1 байт/пиксель,
    # вдвое меньше нагрузка на DVP-шину) — то единственное, что HW-
    # подтверждённо многократно чисто отработало именно внутри приложения.
    # PixelFormat.RGB565 тоже не годится отдельно — известно сломан
    # (радужные артефакты, конвертер сенсора/библиотеки для RGB565 даёт
    # некорректные значения при в остальном верных битах).
    #
    # Цвет ("Цвет" в селекторе) — отдельный, нечастый случай: временно
    # переключаем камеру в YUV422 через reconfigure(), снимаем один кадр,
    # Камера включается лениво и гасится после CAMERA_IDLE_MS простоя:
    # при fb_count=1 сенсор свободно бежит по кадрам (экспозиция, ISP, PLL
    # от XCLK, выгрузка по DVP) всё время, пока инициализирован — отсюда
    # заметный нагрев даже когда никто не снимает. HW-замерено: deinit()
    # 1мс, повторная инициализация 1235мс, первый снимок после неё 818мс —
    # то есть платим ~1.2с только на первом снимке после простоя.
    cam_state = {"cam": None, "last_use": time.ticks_ms()}

    import machine
    cause_code = machine.reset_cause()
    reset_cause = RESET_CAUSES.get(cause_code, str(cause_code))

    # Счётчик ненормальных перезагрузок с момента последней подачи
    # питания. Живёт в RTC-памяти, а не на флеше: при просадке питания
    # запись на флеш — это прямой путь к порче файловой системы, а именно
    # просадку мы и ловим. RTC-домен переживает сброс по питанию-провалу
    # и по сторожевому таймеру, но обнуляется, когда питание снимают
    # совсем, — то есть считает ровно то, что нужно.
    # Плановые перезагрузки (наш /reboot и смена сети) помечают себя
    # префиксом "p" — иначе они считались бы авариями наравне с провалами
    # питания: причина сброса у них одна и та же.
    rtc = machine.RTC()
    raw_mem = bytes(rtc.memory() or b"")
    planned = raw_mem.startswith(b"p")
    try:
        prev = int(raw_mem[1:] if planned else raw_mem or b"0")
    except ValueError:
        prev = 0
    if cause_code == 1:  # POWERON — питание подали заново, счёт с нуля
        abnormal = 0
    elif planned:
        abnormal = prev
    else:
        abnormal = prev + 1
    try:
        rtc.memory(str(abnormal).encode())
    except Exception as e:
        print("rtc memory:", repr(e))
    print("reset cause: %s, аварийных перезагрузок: %d" % (reset_cause, abnormal))

    def _apply_sensor(cam):
        # OV5640 отдаёт кадр зеркальным по горизонтали (HW-подтверждено:
        # на панели текст в кадре читался справа налево). Разворачиваем в
        # самом сенсоре, а не в коде: это регистр, он бесплатный, и кадр
        # приходит уже правильным — значит одинаково верный и в вебе, и на
        # панели, и в истории.
        #
        # В драйвере панели этого делать НЕЛЬЗЯ (пробовали): там разворот
        # переворачивает и подпись с адресом, которая наносится поверх
        # кадра уже после дизеринга, и читать её становится невозможно.
        try:
            cam.set_hmirror(True)
        except Exception as e:
            print("set_hmirror:", repr(e))

    def ensure_cam():
        if cam_state["cam"] is None:
            t0 = time.ticks_ms()
            cam_state["cam"] = Camera(
                pixel_format=PixelFormat.GRAYSCALE,
                frame_size=FrameSize.VGA,
                fb_count=1,
                **CAMERA_PINS
            )
            _apply_sensor(cam_state["cam"])
            print("camera on: %s %dx%d (%d ms)" % (
                cam_state["cam"].get_sensor_name(), PHOTO_WIDTH, PHOTO_HEIGHT,
                time.ticks_diff(time.ticks_ms(), t0)))
        cam_state["last_use"] = time.ticks_ms()
        return cam_state["cam"]

    LED_ON = 1 if LED_ACTIVE_HIGH else 0
    LED_OFF = 0 if LED_ACTIVE_HIGH else 1
    led = Pin(LED_PIN, Pin.OUT, value=LED_OFF)
    # Timer(0), а не Timer(-1): виртуальных таймеров на этом порту нет,
    # только четыре аппаратных (0-3), и -1 отвергается с "invalid Timer
    # number". Остальные три свободны.
    led_timer = Timer(0)
    def _led_off(t):
        t.deinit()
        led.value(LED_OFF)

    def led_signal(ms=LED_SHOT_MS):
        """Подтверждение, что кадр получен: светодиод горит заданное время
        и гаснет сам. Нужно в первую очередь для кнопки на плате — там
        браузера нет и сказать о съёмке больше нечем.

        Повторный вызов во время свечения продлевает его, а не добавляет
        второй таймер: init переустанавливает тот же."""
        led.value(LED_ON)
        led_timer.init(period=ms, mode=Timer.ONE_SHOT, callback=_led_off)

    def camera_off():
        """Гасит камеру немедленно, не дожидаясь простоя.

        Вызывается там, где камера заведомо не нужна: листание истории,
        загрузка своей картинки, перерисовка в другом режиме, обновление
        кода. Из пути СЪЁМКИ убрано намеренно.

        Раньше гасилось и после каждого кадра — ради работы от
        повербанка, по догадке, что камера и обновление экрана вместе
        просаживают питание. Догадка не подтвердилась: настоящей причиной
        было то, что вне дома плата уходила в режим настройки сети. А цену
        платили заметную — каждый снимок становился холодным старом
        сенсора, это около двух секунд вместо трёхсот миллисекунд.

        Простой по-прежнему гасит камеру сам, через CAMERA_IDLE_MS."""
        cam = cam_state["cam"]
        if cam is not None:
            cam.deinit()
            cam_state["cam"] = None
            print("camera off (before panel refresh)")

    def camera_idle_check():
        cam = cam_state["cam"]
        if cam is None:
            return
        if time.ticks_diff(time.ticks_ms(), cam_state["last_use"]) > CAMERA_IDLE_MS:
            cam.deinit()
            cam_state["cam"] = None
            print("camera off (idle)")

    # Спуск без веба — кнопка BOOT (GPIO0), она уже есть на плате.
    # Датчика Холла тут нет: он был только у ESP32 classic, из S2/S3
    # его убрали физически (в ESP-IDF 5 API удалён), в прошивке
    # esp32.hall_sensor отсутствует — проверено на этой плате.
    #
    # GPIO0 — strapping-пин, но это касается только момента загрузки;
    # после старта он обычный вход с подтяжкой, нажатие тянет его к GND.
    #
    # Само нажатие ловим прерыванием, а обрабатываем в цикле сервера:
    # в обработчике прерывания MicroPython нельзя ни выделять память,
    # ни, тем более, снимать кадр и гонять SPI на панель.
    shutter = Pin(0, Pin.IN, Pin.PULL_UP)
    # level — последний признанный уровень; нажатие это 0, отпущено 1.
    # latched ставит прерывание: аппаратура запоминает фронт даже когда
    # опрашивать некому (запись на флеш физически замораживает оба ядра),
    # и нажатие, целиком уместившееся в такое окно, иначе пропало бы.
    btn_state = {"level": 1, "down_ms": 0, "changed_ms": 0,
                 "latched": False, "acted": False, "pending": None}

    def _on_edge(pin):
        btn_state["latched"] = True

    shutter.irq(trigger=Pin.IRQ_FALLING, handler=_on_edge)

    def show_history(step):
        """Листает историю на самой панели, без веба.

        Список идёт от новых к старым, поэтому шаг +1 — это назад по
        времени. Индекс держим отдельно от веб-галереи: там своя
        навигация, и связывать их значило бы, что нажатие на плате
        уводит выбор в браузере."""
        names = persist.list_photos()
        if not names:
            print("история пуста")
            return
        idx = state["hist_idx"]
        if idx < 0:
            idx = 0
        else:
            idx += step
            if idx < 0:
                idx = 0
            elif idx > len(names) - 1:
                idx = len(names) - 1
        state["hist_idx"] = idx
        print("история: %s (%d из %d)" % (names[idx], idx + 1, len(names)))
        camera_off()
        led_signal(LED_NAV_MS)
        path = persist.path_for(names[idx])
        if path:
            persist.show_saved_background(path)

    def button_check():
        """Опрос кнопки, 20 раз в секунду.

        Решение принимается по длительности удержания и в момент
        отпускания: коротко — снимок, 1.2с — предыдущий кадр из истории,
        3с — следующий. О пересечении порогов сообщает светодиод, чтобы
        не приходилось угадывать, сколько ещё держать."""
        now = time.ticks_ms()
        level = shutter.value()

        if level != btn_state["level"]:
            # Уровень должен продержаться, иначе это дребезг контакта.
            if time.ticks_diff(now, btn_state["changed_ms"]) < BUTTON_DEBOUNCE_MS:
                return
            btn_state["changed_ms"] = now
            btn_state["level"] = level
            btn_state["latched"] = False
            if level == 0:
                btn_state["down_ms"] = now
                btn_state["acted"] = False
                btn_state["hinted"] = False
            elif btn_state["down_ms"] and not btn_state["acted"]:
                btn_state["acted"] = True
                # Не выполняем здесь: наблюдение вызывается и посреди
                # отдачи ответа, а снимать в этот момент нельзя.
                btn_state["pending"] = time.ticks_diff(now, btn_state["down_ms"])
            return

        if level == 0 and btn_state["down_ms"] and not btn_state["acted"]:
            # Подсказка светодиодом на пересечении порога: держать
            # дальше — или отпускать.
            held = time.ticks_diff(now, btn_state["down_ms"])
            if held >= BUTTON_HOLD_PREV_MS and not btn_state.get("hinted"):
                btn_state["hinted"] = True
                led_signal(LED_NAV_MS)
            return

        if btn_state["latched"] and level == 1:
            # Прерывание видело нажатие, которого опрос не застал: оно
            # целиком уместилось в окно, когда оба ядра стояли на записи
            # флеша. Считаем коротким — длительность узнать уже негде.
            btn_state["latched"] = False
            btn_state["acted"] = True
            btn_state["pending"] = 0

    def button_dispatch():
        """Выполняет отложенное действие кнопки. Вызывается только из
        холостого хода серверного цикла — то есть когда ответ клиенту уже
        отдан и снимать безопасно."""
        held = btn_state["pending"]
        if held is None:
            return
        btn_state["pending"] = None
        button_act(held)

    def button_act(held_ms):
        # Что плата решила по нажатию — видно в /status: консоли у неё в
        # автономном режиме нет, а угадывать по симптомам дорого.
        state["last_action"] = "удержание %dмс -> " % held_ms
        if held_ms >= BUTTON_HOLD_NEXT_MS:
            state["last_action"] += "вперёд по истории"
            show_history(-1)
            return
        if held_ms >= BUTTON_HOLD_PREV_MS:
            state["last_action"] += "назад по истории"
            show_history(1)
            return
        state["last_action"] += "съёмка"
        print("shutter button pressed")
        try:
            # Отсчёт задержки — от момента нажатия: пока кнопку держали,
            # человек уже ждал, и вычитать это время дважды незачем.
            capture_now(btn_state["down_ms"] or None)
            state["pending_save"] = False
            # Новый кадр сбрасывает листание: следующее удержание должно
            # начинать с самого свежего снимка, а не с того места, где
            # человек листал до съёмки.
            state["hist_idx"] = -1
            ok = persist.render_and_save_background(
                state["raw"], PHOTO_WIDTH, PHOTO_HEIGHT,
                DISPLAY_WIDTH, DISPLAY_HEIGHT, state["label"]
            )
            if ok == "queued":
                state["last_action"] += " (в очереди, экран ещё занят)"
            elif not ok:
                state["last_action"] += " (не выведен)"
        except Exception as e:
            state["last_action"] += " ошибка %r" % e
            print("shutter error:", repr(e))

    def reboot_check():
        at = state["reboot_at"]
        if at is not None and time.ticks_diff(time.ticks_ms(), at) >= 0:
            import machine
            print("rebooting after wifi change")
            machine.reset()

    # Самый большой промежуток между двумя опросами кнопки. Это и есть
    # настоящая мера отзывчивости спуска: пока главный поток не получил
    # управление, нажатие некому заметить. Задержку HTTP для этого мерить
    # нельзя — она включает повторные передачи TCP и раздувает короткую
    # заморозку ядер до секунд.
    idle_gap = {"last": time.ticks_ms(), "max": 0}

    def button_tick():
        """Только наблюдение за кнопкой. Зовётся и из холостого хода, и
        между порциями отдаваемого ответа."""
        now = time.ticks_ms()
        gap = time.ticks_diff(now, idle_gap["last"])
        idle_gap["last"] = now
        if gap > idle_gap["max"]:
            idle_gap["max"] = gap
        button_check()

    # Дизеринг зовёт наблюдение за кнопкой между полосами: полный кадр
    # считается около двух секунд, и в главном потоке (превью для веба)
    # это было окно, в которое нажатие не замечалось.
    dither.on_yield = button_tick

    def on_idle():
        button_tick()
        button_dispatch()
        reboot_check()
        camera_idle_check()

    # HW-подтверждено: сам захват камеры быстрый и чистый (~400мс).
    # История снимков пишется на flash в фоновом потоке (persist.py), НО:
    # запись flash на ESP32 физически останавливает выполнение на ОБОИХ
    # ядрах на время самой операции (кэш инструкций общий с флешем) —
    # поток тут не даёт настоящего параллелизма, только избавляет от
    # необходимости ждать записи явно перед ответом. Поэтому пишем не
    # полный кадр, а уже уменьшенный+дизеренный 400x300 1bpp (~15КБ,
    # ~270мс) — это и есть формат под e-paper.
    # mode — режим панели, который выбран в вебе; кнопкой на плате
    # снимаем в нём же, чтобы физический спуск и веб не расходились.
    # На кадре — только последняя группа адреса: остальное в домашней
    # сети и так одинаково, а шрифт тут один и самый мелкий (8x8), так
    # что короткая подпись занимает вчетверо меньше места.
    state = {"raw": None, "pending_save": False,
             "label": (ip or ap_ip or "?").split(".")[-1],
             "delay_ms": DEFAULT_DELAY_MS,
             "seq": 0, "reboot_at": None, "hist_idx": -1,
             "last_action": "нажатий не было"}

    def capture_now(since_ms=None):
        """since_ms — момент, от которого считается задержка автоспуска:
        нажатие кнопки или приход запроса.

        Камеру будим ДО отсчёта, и отсчёт ждём не целиком, а лишь остаток.
        Холодный старт сенсора это ~1.6с, то есть он сам дольше
        полусекундной задержки, — а раньше они складывались, хотя человек
        с момента нажатия всё это время и так ждал."""
        # Номер кадра нужен странице: снимок могли сделать кнопкой на
        # плате, и браузер узнаёт об этом только опросом.
        state["seq"] += 1
        if since_ms is None:
            since_ms = time.ticks_ms()
        cam = ensure_cam()
        left = state["delay_ms"] - time.ticks_diff(time.ticks_ms(), since_ms)
        if left > 0:
            time.sleep_ms(left)
        cam_state["last_use"] = time.ticks_ms()
        # С fb_count=1 сенсор непрерывно пишет в один и тот же буфер в
        # фоне — первый capture() может вернуть кадр, начатый ДО нажатия
        # кнопки (отсюда "фото из прошлого"). Один кадр отбрасываем, чтобы
        # взять тот, что начал экспонироваться уже после запроса.
        cam.capture()
        cam.free_buffer()
        img = bytes(cam.capture())
        cam.free_buffer()
        state["raw"] = img
        led_signal()
        # Сохранение в историю НЕ запускаем здесь: даже в отдельном потоке
        # оно конкурирует за процессор с отдачей превью, а сама запись на
        # flash вообще замораживает оба ядра. Строго "сначала показываем,
        # потом пишем" — страница дёргает /save сама, когда оба превью уже
        # загрузились (см. CAMERA_PAGE).
        state["pending_save"] = True

    def ensure_frame():
        """Первый кадр снимаем по запросу, а не на старте.

        Раньше capture_now() стоял прямо здесь, в загрузке: инициализация
        сенсора (~1.2с, пиковый ток) накладывалась на подъём Wi-Fi, и
        момент включения был самым тяжёлым по питанию за всю работу платы.
        От слабого источника это ровно та точка, где напряжение просядет и
        сработает защита от понижения."""
        if state["raw"] is None:
            capture_now()

    def security_note(cfg):
        """Честно говорит, что открыто. Пароль точки доступа по умолчанию
        лежит в публичном репозитории, а заливка кода без токена означает,
        что любой в радиусе Wi-Fi может выполнить на плате свой код."""
        warn = []
        if not cfg.get("ap_password"):
            warn.append("пароль точки доступа стандартный и есть в публичном репозитории")
        if not cfg.get("push_token"):
            warn.append("заливка кода не требует токена")
        if not warn:
            return "Пароль точки доступа и токен заливки заданы."
        return "Внимание: " + "; ".join(warn) + "."

    def handle_index(query, headers, body=None):
        # .replace, а не .format: в CSS странице полно фигурных скобок,
        # format на них падает с KeyError.
        cfg = wifi_manager.load_config()
        page = (CAMERA_PAGE
                .replace("{cur_ssid}", cfg.get("ssid", "") or "не задана")
                .replace("{cur_ip}", ip or "не подключено")
                .replace("{cur_ap}", ap_ip or "-")
                .replace("{ap_ssid}", wifi_manager.AP_SSID)
                .replace("{security}", security_note(cfg)))
        return 200, "text/html", page.encode()

    def handle_capture(query, headers, body=None):
        if "delay" in query:
            try:
                sec = float(query["delay"])
            except ValueError:
                sec = 0.0
            # Ограничение сверху не для красоты: сервер однопоточный и на
            # время задержки не отвечает вообще ни на что.
            state["delay_ms"] = max(0, min(30000, int(sec * 1000)))
        t0 = time.ticks_ms()
        try:
            capture_now()
        except Exception as e:
            return 200, "text/plain", ("Ошибка съёмки: %r" % e).encode()
        dt = time.ticks_diff(time.ticks_ms(), t0)
        return 200, "text/plain", ("Готово (%d мс)" % dt).encode()

    def handle_dither(query, headers, body=None):
        # Единственный алгоритм: Флойд-Стейнберг в 4 градации. На выходе
        # номера уровней 0..3, поэтому BMP четырёхбитный — ровно тот набор
        # тонов, который реально покажет панель.
        levels = dither.floyd_steinberg_4g(state["raw"], PHOTO_WIDTH, PHOTO_HEIGHT)
        out = gray4_to_bmp(levels, PHOTO_WIDTH, PHOTO_HEIGHT)
        return 200, "image/bmp", out

    def handle_epaper_preview(query, headers, body=None):
        # Ровно то же самое, что уйдёт на панель и в историю (persist.py):
        # обрезка по краям до 400x300 (не сжатие) + Флойд-Стейнберг в
        # выбранном режиме. Считаем здесь заново, а не переиспользуем
        # результат фонового потока: превью показывается ДО того, как тот
        # вообще запустится.
        ensure_frame()
        cropped = dither.resize_crop_nearest(state["raw"], PHOTO_WIDTH, PHOTO_HEIGHT, DISPLAY_WIDTH, DISPLAY_HEIGHT)
        levels = dither.floyd_steinberg_4g(cropped, DISPLAY_WIDTH, DISPLAY_HEIGHT)
        epaper.overlay_text(levels, state["label"], fg=0, bg=3)
        out = gray4_to_bmp(levels, DISPLAY_WIDTH, DISPLAY_HEIGHT)
        return 200, "image/bmp", out

    def handle_wifi_save(query, headers, body=None):
        ssid = query.get("ssid", "")
        if not ssid:
            return 200, "text/html", "<h3>Введите SSID</h3>".encode()
        wifi_manager.save_config(
            ssid, query.get("password", ""),
            ap_password=query.get("ap_password", ""),
            push_token=query.get("push_token", ""),
        )
        mark_planned_reboot()
        # Перезагружаемся не здесь, а из холостого хода цикла: ответ
        # странице ещё не отправлен, его отправляет run_server уже после
        # возврата из обработчика.
        state["reboot_at"] = time.ticks_add(time.ticks_ms(), 1500)
        return 200, "text/html", (
            "<h3>Сохранено: %s</h3><p>Перезагружаюсь. Если сеть не "
            "поднимется, плата останется доступна по точке доступа "
            "%s.</p>" % (ssid, wifi_manager.AP_SSID)).encode()

    def handle_push(query, headers, body=None):
        # Заливка исходников по сети. Появилась не от хорошей жизни:
        # драйвер USB-моста на маке регулярно залипает и перестаёт менять
        # скорость порта, а плата при этом жива и доступна по Wi-Fi.
        token = wifi_manager.push_token()
        if token and query.get("token", "") != token:
            return 403, "text/plain", "Неверный токен заливки".encode()
        name = query.get("path", "")
        if not valid_module_name(name):
            return 400, "text/plain", ("Недопустимое имя: %r" % name).encode()
        if not body:
            return 400, "text/plain", "Пустое тело запроса".encode()
        try:
            with open("/" + name, "wb") as f:
                f.write(body)
        except OSError as e:
            return 500, "text/plain", ("Ошибка записи: %r" % e).encode()
        return 200, "text/plain", ("записано %s, %d Б" % (name, len(body))).encode()

    def mark_planned_reboot():
        try:
            machine.RTC().memory(b"p" + str(abnormal).encode())
        except Exception:
            pass

    def handle_ota_check(query, headers, body=None):
        import json
        try:
            import ota
            info = ota.check()
        except Exception as e:
            return 200, "application/json", json.dumps({"error": repr(e)}).encode()
        # Манифест наружу не отдаём: он большой, а странице нужны только
        # номера версий.
        return 200, "application/json", json.dumps({
            "current_version": info["current_version"],
            "available_version": info["available_version"],
            "update_available": info["update_available"],
            "source": info["source"],
        }).encode()

    def handle_ota_apply(query, headers, body=None):
        try:
            import ota
            info = ota.check()
            if not info["update_available"]:
                return 200, "text/plain", "Обновлений нет".encode()
            camera_off()
            changed = ota.apply(info["manifest"], info["source"])
        except Exception as e:
            return 200, "text/plain", ("Обновление не удалось: %r" % e).encode()
        mark_planned_reboot()
        state["reboot_at"] = time.ticks_add(time.ticks_ms(), 1500)
        return 200, "text/plain", (
            "Обновлено до версии %d, файлов заменено %d. Перезагружаюсь."
            % (info["available_version"], changed)).encode()

    def handle_reboot(query, headers, body=None):
        mark_planned_reboot()
        state["reboot_at"] = time.ticks_add(time.ticks_ms(), 1000)
        return 200, "text/plain", b"reboot"

    def handle_upload(query, headers, body=None):
        # Браузер присылает уже готовый серый буфер 400x300: он же и
        # декодировал файл, и обрезал его по краям до 4:3. На плате
        # остаётся то, ради чего всё и затевалось, — дизеринг в 4 градации,
        # вывод на панель и запись в историю.
        n = DISPLAY_WIDTH * DISPLAY_HEIGHT
        if body is None or len(body) != n:
            got = 0 if body is None else len(body)
            return 400, "text/plain", (
                "Ожидал %d байт (серый %dx%d), пришло %d"
                % (n, DISPLAY_WIDTH, DISPLAY_HEIGHT, got)).encode()
        camera_off()
        # Подпись с адресом не наносим: это не снимок с камеры, а картинка,
        # которую человек принёс сам.
        if persist.render_and_save_background(
            body, DISPLAY_WIDTH, DISPLAY_HEIGHT, DISPLAY_WIDTH, DISPLAY_HEIGHT,
            "", True
        ):
            return 200, "text/plain", "Изображение принято, вывожу на экран".encode()
        return 200, "text/plain", "Экран занят, попробуй ещё раз".encode()

    def _ota_version():
        try:
            import ota
            return ota.current_version()
        except Exception:
            return None

    def _pm():
        try:
            import network
            return network.WLAN(network.STA_IF).config("pm")
        except Exception:
            return None

    def _txpower():
        try:
            import network
            return network.WLAN(network.STA_IF).config("txpower")
        except Exception:
            return None

    def handle_status(query, headers, body=None):
        import json
        if query.get("reset_gap"):
            idle_gap["max"] = 0
        return 200, "application/json", json.dumps({
            "seq": state["seq"],
            "ip": ip,
            "ap": ap_ip,
            # Причина последнего старта. Читается по сети специально:
            # когда плата питается не от компьютера, USB-консоли нет, а
            # BROWNOUT здесь — прямая улика просадки питания.
            "reset": reset_cause,
            "abnormal_resets": abnormal,
            "hist_idx": state["hist_idx"],
            "last_action": state["last_action"],
            "last_error": persist.last_error,
            "max_idle_gap_ms": idle_gap["max"],
            "worker": persist.state(),
            "version": _ota_version(),
            "txpower": _txpower(),
            "pm": _pm(),
        }).encode()

    def handle_delete(query, headers, body=None):
        name = query.get("name", "")
        if persist.delete_photo(name):
            return 200, "text/plain", ("Удалён %s" % name).encode()
        return 404, "text/plain", "Нет такого снимка".encode()

    def handle_render(query, headers, body=None):
        # Перерисовка уже снятого кадра в другом режиме. В историю ничего
        # не пишем: снимок тот же самый, копии в галерее не нужны.
        if state["raw"] is None:
            return 200, "text/plain", "Ещё нечего показывать".encode()
        camera_off()
        if persist.render_and_save_background(
            state["raw"], PHOTO_WIDTH, PHOTO_HEIGHT, DISPLAY_WIDTH, DISPLAY_HEIGHT,
            state["label"], False
        ):
            return 200, "text/plain", "Перерисовываю экран".encode()
        return 200, "text/plain", "Экран занят, попробуй ещё раз".encode()

    def handle_save(query, headers, body=None):
        # Вызывается страницей уже ПОСЛЕ того, как оба превью показаны.
        # Отсюда и вывод на саму панель: обновление e-paper — это секунды,
        # в путь захвата оно попасть не должно.
        if not state["pending_save"]:
            return 200, "text/plain", b"nothing to save"
        state["pending_save"] = False
        persist.render_and_save_background(
            state["raw"], PHOTO_WIDTH, PHOTO_HEIGHT, DISPLAY_WIDTH, DISPLAY_HEIGHT,
            state["label"]
        )
        return 200, "text/plain", b"rendering"

    def handle_list(query, headers, body=None):
        import json
        items = [{"name": n} for n in persist.list_photos()]
        return 200, "application/json", json.dumps(items).encode()

    def handle_photo(query, headers, body=None):
        path = persist.path_for(query.get("name", ""))
        if path is None:
            return 404, "text/plain", b"no such photo"
        # str в теле ответа = путь к файлу, web_app стримит его чанками
        return 200, "image/bmp", path

    def handle_show(query, headers, body=None):
        name = query.get("name", "")
        path = persist.path_for(name)
        if path is None:
            return 404, "text/plain", b"no such photo"
        if persist.show_saved_background(path):
            return 200, "text/plain", ("Вывожу %s на экран" % name).encode()
        return 200, "text/plain", "Панель занята, попробуй ещё раз".encode()

    routes = {
        ("GET", "/"): handle_index,
        ("GET", "/list"): handle_list,
        ("GET", "/photo"): handle_photo,
        ("GET", "/show"): handle_show,
        ("GET", "/status"): handle_status,
        ("POST", "/upload"): handle_upload,
        ("POST", "/push"): handle_push,
        ("GET", "/reboot"): handle_reboot,
        ("GET", "/ota_check"): handle_ota_check,
        ("GET", "/ota_apply"): handle_ota_apply,
        ("GET", "/wifi_save"): handle_wifi_save,
        ("GET", "/delete"): handle_delete,
        ("GET", "/render"): handle_render,
        ("GET", "/capture"): handle_capture,
        ("GET", "/dither"): handle_dither,
        ("GET", "/epaper_preview"): handle_epaper_preview,
        ("GET", "/save"): handle_save,
    }
    # 0.1с, а не 0.2: в этот такт опрашивается кнопка, и половина такта
    # прибавляется к задержке между нажатием и кадром.
    # 0.05с: в этот такт опрашивается кнопка, и такт должен быть заметно
    # короче самого короткого осмысленного нажатия, иначе его можно
    # проспать целиком.
    run_server(routes, on_idle=on_idle, idle_interval_sec=0.05,
               on_tick=button_tick)


def main():
    """Камера поднимается ВСЕГДА, нашлась домашняя сеть или нет.

    Раньше при неудачном подключении плата уходила в отдельный режим
    первичной настройки, где работал урезанный сервер: только форма ввода
    пароля, без камеры, без кнопки и без экрана. Вне дома это выглядело
    как полностью мёртвая плата — светодиод горит, а камеры нет. Форма
    настройки сети теперь и так есть внизу главной страницы, так что
    отдельный режим не нужен.

    Точка доступа поднимается в любом случае, поэтому камера доступна
    даже там, где знакомых сетей нет вовсе."""
    cfg = wifi_manager.load_config()

    ip = ""
    if cfg.get("ssid"):
        print("connecting to", cfg["ssid"], "...")
        try:
            ip = wifi_manager.connect_sta(cfg["ssid"], cfg["password"], timeout_sec=20) or ""
        except Exception as e:
            # Ни одна ошибка сети не должна ронять приложение: плата
            # носимая, упасть в REPL для неё значит умереть насовсем.
            print("ошибка подключения:", repr(e))
            ip = ""
        if ip:
            print("connected, IP =", ip)
        else:
            print("домашняя сеть недоступна — работаем только через точку доступа")
    else:
        print("сеть не настроена — работаем только через точку доступа")

    ap_ip = ""
    try:
        ap = wifi_manager.ensure_ap(ip)
        ap_ip = ap.ifconfig()[0]
        print("AP '%s': http://%s/" % (wifi_manager.AP_SSID, ap_ip))
    except Exception as e:
        print("AP start failed:", repr(e))

    if ip:
        print("open http://%s/ in a browser on the same network" % ip)
    run_camera_server(ip, ap_ip)


main()
