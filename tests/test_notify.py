"""Уведомления для острова: разбор XML, имя программы, фильтр и чтение базы wpndatabase.db (на поддельной базе)."""
import sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from karina import notify as n, island_tools as t

XML = ('<toast launch="x"><visual><binding template="ToastGeneric"><text>Анна</text><text>Привет, ты где?</text>'
       '<text placement="attribution">Telegram</text></binding></visual></toast>')


def test_parse_payload_variants():
    assert n.parse_payload(XML) == ("Анна", "Привет, ты где?")
    assert n.parse_payload(XML.encode("utf-8")) == ("Анна", "Привет, ты где?")
    assert n.parse_payload(("﻿" + XML).encode("utf-16")) == ("Анна", "Привет, ты где?")
    assert n.parse_payload(b"not xml") == ("", "") and n.parse_payload(None) == ("", "")


def test_app_title_and_filter():
    assert n.app_title("TelegramMessengerLLP.TelegramDesktop") == "Telegram"
    assert n.app_title("5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App") == "WhatsApp"
    assert n.app_title("Microsoft.Windows.Explorer") == "Explorer"
    conf = {"enabled": True, "mode": "messengers", "apps": ["mailclient"]}
    assert n.wanted("TelegramMessengerLLP.TelegramDesktop", conf)
    assert n.wanted("Acme.MailClient", conf)
    assert not n.wanted("Microsoft.Windows.Explorer", conf)
    assert n.wanted("Microsoft.Windows.Explorer", {**conf, "mode": "all"})
    assert not n.wanted("TelegramMessengerLLP.TelegramDesktop", {**conf, "enabled": False})


def test_read_new_from_fake_db(tmp_path):
    db = tmp_path / "wpndatabase.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE NotificationHandler (RecordId INTEGER PRIMARY KEY, PrimaryId TEXT)")
    con.execute("CREATE TABLE Notification (Id INTEGER PRIMARY KEY, HandlerId INTEGER, Type TEXT, Payload BLOB)")
    con.execute("INSERT INTO NotificationHandler VALUES (1, 'TelegramMessengerLLP.TelegramDesktop')")
    con.execute("INSERT INTO Notification VALUES (5, 1, 'toast', ?)", (XML.encode("utf-8"),))
    con.execute("INSERT INTO Notification VALUES (6, 1, 'tile', ?)", (b"<tile/>",))
    con.commit(); con.close()
    assert n.max_id(db) == 5
    got = n.read_new(db, 0)
    assert got == [{"id": 5, "app_id": "TelegramMessengerLLP.TelegramDesktop", "title": "Анна", "text": "Привет, ты где?"}]
    assert n.read_new(db, 5) == []


def test_island_setting_notify_default_and_clean():
    assert t.clean_settings({}, {})["notify"] is True
    assert t.clean_settings({"notify": 0}, {})["notify"] is False
