import network
import time
import ujson as json

CONFIG_PATH = "/wifi_config.json"
# Адрес точки доступа. Один и тот же всегда — камера носимая, и адрес,
# который зависит от того, нашлась ли домашняя сеть, пришлось бы каждый
# раз выяснять заново.
#
# Именно 192.168.4.x, а не 192.168.1.x, как было в первой версии: дома
# сеть тоже 192.168.1.0/24, и два интерфейса с одинаковым префиксом ESP32
# развести не может — маршрут выбирается по совпадению префикса, и ответы
# уходят не в тот интерфейс.
AP_IP = "192.168.4.1"
# Мощность передатчика, дБм. По умолчанию радио выдаёт 20 дБм (100 мВт) —
# это самый резкий импульс тока на плате, куда острее камеры и экрана.
# 13 дБм (20 мВт) впятеро меньше по мощности, для домашней сети с запасом,
# а пик потребления при передаче заметно ниже. Ради работы от повербанка
# это первое, чем стоит жертвовать: дальность нам не нужна.
TX_POWER_DBM = 13

# Режим энергосбережения радио. PM_POWERSAVE (2) агрессивнее штатного
# PM_PERFORMANCE (1): радио дольше спит между маячками точки доступа.
# Платим задержкой отклика, выигрываем в потреблении — камера питается
# от батареи, и это важнее скорости ответа веб-страницы.
#
# Пробовали и обратное, PM_NONE, чтобы повербанк не отключался по низкой
# нагрузке. Убрано: причина отказов была не в этом, а в том, что вне дома
# плата уходила в режим настройки сети.
WIFI_PM = 2

AP_SSID = "ePaperCamera-Setup"
# Пароль точки доступа по умолчанию. Он лежит в публичном репозитории,
# то есть секретом не является вообще — это заведомо временное значение
# на первое включение. Свой задаётся в веб-интерфейсе и хранится в
# wifi_config.json, который в репозиторий не попадает.
AP_PASSWORD_DEFAULT = "12345678"


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"ssid": "", "password": ""}


def ap_password():
    """Свой пароль точки доступа, если задан. Короче восьми символов Wi-Fi
    не принимает, поэтому такие молча игнорируем — иначе точка не
    поднялась бы вообще и до платы стало бы не добраться."""
    pwd = load_config().get("ap_password") or ""
    return pwd if len(pwd) >= 8 else AP_PASSWORD_DEFAULT


def push_token():
    """Токен для заливки кода по сети. Пусто — проверки нет (так плата и
    ведёт себя из коробки, чтобы первое включение ничего не требовало)."""
    return load_config().get("push_token") or ""


def save_config(ssid, password, ap_password=None, push_token=None):
    """Дописывает, а не перезаписывает: в этом же файле живут пароль точки
    доступа и токен заливки, и сохранение домашней сети не должно их
    сносить. Пустые значения означают "не менять" — иначе пустое поле
    формы стирало бы уже заданный пароль."""
    cfg = load_config()
    cfg["ssid"] = ssid
    # Пустой пароль — "не менять", как и у остальных полей. Иначе отправка
    # формы ради смены только пароля точки доступа стирала бы пароль
    # домашней сети, и плата теряла бы сеть на следующей же загрузке.
    if password:
        cfg["password"] = "" if password == "-" else password
    elif "password" not in cfg:
        cfg["password"] = ""
    # Одиночный минус — "снять". Без такого условного значения пустое
    # поле формы означало бы "не менять", и заданный однажды пароль или
    # токен убрать через интерфейс было бы уже нельзя.
    if ap_password:
        cfg["ap_password"] = "" if ap_password == "-" else ap_password
    if push_token:
        cfg["push_token"] = "" if push_token == "-" else push_token
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f)


def ensure_ap(sta_ip=None):
    """Поднимает точку доступа, не трогая STA: ESP32 умеет держать оба
    интерфейса разом (AP+STA), причём AP переезжает на канал STA сам."""
    return start_ap(AP_IP)


def limit_tx_power(wlan):
    """Снижает мощность передатчика. Молча переживает прошивки, где
    параметра нет: это оптимизация питания, а не условие работы."""
    try:
        wlan.config(txpower=TX_POWER_DBM)
    except Exception as e:
        print("txpower не установлен:", repr(e))


def start_ap(ip=AP_IP):
    ap = network.WLAN(network.AP_IF)
    ap.active(False)
    time.sleep_ms(100)
    ap.ifconfig((ip, "255.255.255.0", ip, ip))
    ap.active(True)
    ap.config(essid=AP_SSID, password=ap_password(), authmode=network.AUTH_WPA_WPA2_PSK)
    limit_tx_power(ap)
    time.sleep_ms(200)
    print("AP ifconfig:", ap.ifconfig())
    return ap


def connect_sta(ssid, password, timeout_sec=20):
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    # До connect(), а не после: самая длинная непрерывная передача — это
    # как раз присоединение к сети, и происходит она при загрузке, когда
    # источник питания ещё не "раскачан".
    limit_tx_power(sta)
    if not sta.isconnected():
        # Драйвер Wi-Fi умеет отвечать "Wifi Internal State Error", если
        # его дёрнуть, пока он ещё не разобрался с предыдущим состоянием
        # (HW-подтверждено: так упала загрузка после мягкой перезагрузки,
        # когда радио оставалось поднятым с прошлого раза). Одна честная
        # попытка с полным перезапуском интерфейса это лечит; ошибка
        # наружу не выходит ни при каких условиях — без сети камера
        # обязана работать, а не падать в REPL.
        try:
            sta.connect(ssid, password)
        except OSError as e:
            print("connect не принят (%r), перезапускаю интерфейс" % e)
            try:
                sta.active(False)
                time.sleep_ms(500)
                sta.active(True)
                time.sleep_ms(200)
                limit_tx_power(sta)
                sta.connect(ssid, password)
            except OSError as e2:
                print("connect не удался:", repr(e2))
                return None
        t0 = time.time()
        while not sta.isconnected():
            if time.time() - t0 > timeout_sec:
                try:
                    sta.active(False)
                except OSError:
                    pass
                return None
            time.sleep_ms(200)
    # Энергосбережение включаем ПОСЛЕ присоединения, а не до: присоединение
    # — самый длинный непрерывный обмен с точкой доступа, и засыпающее
    # посреди него радио может его сорвать.
    try:
        sta.config(pm=WIFI_PM)
    except Exception as e:
        print("pm не установлен:", repr(e))
    return sta.ifconfig()[0]
