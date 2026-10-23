"""The vocabulary, in six languages.

What is here is the vocabulary of the trade — the words that appear on a
cutting list, a job card, a drawing title block, a quotation and the phone in a
fitter's hand. That is the set worth translating properly, and it is small
enough to keep accurate.

What is not here, on purpose: article numbers, system series, machine codes,
file format names and layer names. Those are identifiers. Translating an
identifier produces a different identifier, and a cutting list that says
"מסגרת-70" where the supplier's catalogue says "MB70-FRAME" is a cutting list
nobody can order from.

Keys are dotted and grouped by where they are used. A key with no entry for the
requested language falls back to English rather than showing the key itself —
an operator seeing ``stage.machined`` learns nothing, while seeing "machined"
at least tells them what happened.
"""

from __future__ import annotations

from .locale import Language

#: key -> {language: text}. English is required on every key; it is the
#: fallback, so a missing translation degrades to a word rather than a token.
MESSAGES: dict[str, dict[str, str]] = {
    # -- production stages ------------------------------------------------- #
    "stage.planned": {
        "en": "planned", "he": "מתוכנן", "ar": "مُخطَّط", "ru": "запланировано",
        "it": "pianificato", "es": "planificado",
    },
    "stage.cut": {
        "en": "cut", "he": "נחתך", "ar": "مقصوص", "ru": "нарезано",
        "it": "tagliato", "es": "cortado",
    },
    "stage.machined": {
        "en": "machined", "he": "עובד", "ar": "مُشغَّل", "ru": "обработано",
        "it": "lavorato", "es": "mecanizado",
    },
    "stage.assembled": {
        "en": "assembled", "he": "הורכב", "ar": "مُجمَّع", "ru": "собрано",
        "it": "assemblato", "es": "ensamblado",
    },
    "stage.glazed": {
        "en": "glazed", "he": "זוגג", "ar": "مُزجَّج", "ru": "остеклено",
        "it": "vetrato", "es": "acristalado",
    },
    "stage.inspected": {
        "en": "inspected", "he": "נבדק", "ar": "مفحوص", "ru": "проверено",
        "it": "controllato", "es": "inspeccionado",
    },
    "stage.shipped": {
        "en": "shipped", "he": "נשלח", "ar": "مُرسَل", "ru": "отгружено",
        "it": "spedito", "es": "enviado",
    },
    "stage.rework": {
        "en": "rework", "he": "לתיקון", "ar": "لإعادة العمل", "ru": "на доработку",
        "it": "da rilavorare", "es": "retrabajo",
    },
    "stage.scrapped": {
        "en": "scrapped", "he": "פסול", "ar": "مرفوض", "ru": "брак",
        "it": "scartato", "es": "desechado",
    },

    # -- how a leaf opens --------------------------------------------------- #
    "opening.fixed": {
        "en": "fixed", "he": "קבוע", "ar": "ثابت", "ru": "глухое",
        "it": "fisso", "es": "fijo",
    },
    "opening.casement": {
        "en": "casement", "he": "פתיחה", "ar": "مفصلي", "ru": "распашное",
        "it": "a battente", "es": "practicable",
    },
    "opening.tilt_turn": {
        "en": "tilt and turn", "he": "נטוי-פתוח", "ar": "قلاب ودوار",
        "ru": "поворотно-откидное", "it": "anta-ribalta", "es": "oscilobatiente",
    },
    "opening.top_hung": {
        "en": "top hung", "he": "פתיחה עליונה", "ar": "معلق علوي",
        "ru": "верхнеподвесное", "it": "a vasistas", "es": "abatible superior",
    },
    "opening.bottom_hung": {
        "en": "bottom hung", "he": "פתיחה תחתונה", "ar": "معلق سفلي",
        "ru": "нижнеподвесное", "it": "a ribalta", "es": "abatible inferior",
    },
    "opening.sliding": {
        "en": "sliding", "he": "הזזה", "ar": "منزلق", "ru": "раздвижное",
        "it": "scorrevole", "es": "corredera",
    },
    "opening.lift_slide": {
        "en": "lift and slide", "he": "הזזה מורמת", "ar": "رفع وانزلاق",
        "ru": "подъёмно-раздвижное", "it": "alzante scorrevole", "es": "elevable corredera",
    },
    "opening.door": {
        "en": "door", "he": "דלת", "ar": "باب", "ru": "дверь",
        "it": "porta", "es": "puerta",
    },
    "opening.pivot": {
        "en": "pivot", "he": "ציר מרכזי", "ar": "محوري", "ru": "поворотное на оси",
        "it": "a bilico", "es": "pivotante",
    },

    # -- the parts ---------------------------------------------------------- #
    "member.frame": {
        "en": "frame", "he": "משקוף", "ar": "إطار", "ru": "рама",
        "it": "telaio", "es": "marco",
    },
    "member.sash": {
        "en": "sash", "he": "כנף", "ar": "درفة", "ru": "створка",
        "it": "anta", "es": "hoja",
    },
    "member.mullion": {
        "en": "mullion", "he": "עמוד", "ar": "قائم", "ru": "стойка",
        "it": "montante", "es": "montante",
    },
    "member.transom": {
        "en": "transom", "he": "קורה", "ar": "عارضة", "ru": "ригель",
        "it": "traverso", "es": "travesaño",
    },
    "member.bead": {
        "en": "glazing bead", "he": "סרגל זיגוג", "ar": "شريحة تزجيج",
        "ru": "штапик", "it": "fermavetro", "es": "junquillo",
    },
    "member.threshold": {
        "en": "threshold", "he": "סף", "ar": "عتبة", "ru": "порог",
        "it": "soglia", "es": "umbral",
    },
    "member.glass": {
        "en": "glass", "he": "זכוכית", "ar": "زجاج", "ru": "стекло",
        "it": "vetro", "es": "vidrio",
    },
    "member.gasket": {
        "en": "gasket", "he": "אטם", "ar": "حشية", "ru": "уплотнитель",
        "it": "guarnizione", "es": "junta",
    },
    "member.hardware": {
        "en": "hardware", "he": "פרזול", "ar": "إكسسوارات", "ru": "фурнитура",
        "it": "ferramenta", "es": "herrajes",
    },

    # -- what the geometry reader finds on a section ------------------------ #
    "feature.euro_groove": {
        "en": "Euro groove", "he": "חריץ אירו", "ar": "مجرى أوروبي",
        "ru": "европаз", "it": "cava europea", "es": "ranura europea",
    },
    "feature.glazing_rebate": {
        "en": "glazing rebate", "he": "שקע זיגוג", "ar": "مجرى التزجيج",
        "ru": "фальц остекления", "it": "battuta del vetro", "es": "galce de acristalamiento",
    },
    "feature.gasket_groove": {
        "en": "gasket groove", "he": "חריץ אטם", "ar": "مجرى الحشية",
        "ru": "паз уплотнителя", "it": "cava guarnizione", "es": "ranura de junta",
    },
    "feature.screw_port": {
        "en": "screw port", "he": "תעלת בורג", "ar": "قناة البرغي",
        "ru": "винтовой канал", "it": "cava vite", "es": "canal de tornillo",
    },
    "feature.thermal_break_channel": {
        "en": "polyamide channel", "he": "ערוץ פוליאמיד", "ar": "قناة البولي أميد",
        "ru": "канал термовставки", "it": "cava poliammide", "es": "canal de poliamida",
    },
    "feature.bead_clip": {
        "en": "bead clip", "he": "נעילת סרגל", "ar": "مشبك الشريحة",
        "ru": "защёлка штапика", "it": "aggancio fermavetro", "es": "clip de junquillo",
    },
    "feature.pocket": {
        "en": "recess", "he": "חריץ", "ar": "تجويف", "ru": "паз",
        "it": "incavo", "es": "rebaje",
    },

    # -- kinds of system ---------------------------------------------------- #
    "family.folding": {
        "en": "folding", "he": "אקורדיון", "ar": "قابل للطي", "ru": "складное",
        "it": "a libro", "es": "plegable",
    },
    "family.partition": {
        "en": "office partition", "he": "מחיצות משרד", "ar": "قواطع مكتبية",
        "ru": "офисная перегородка", "it": "parete divisoria", "es": "mampara de oficina",
    },
    "family.curtain_wall": {
        "en": "curtain wall", "he": "קיר מסך", "ar": "حائط ستائري",
        "ru": "навесной фасад", "it": "facciata continua", "es": "muro cortina",
    },
    "family.skylight": {
        "en": "skylight", "he": "סקיילייט", "ar": "منور سقفي", "ru": "зенитный фонарь",
        "it": "lucernario", "es": "lucernario",
    },
    "family.shading": {
        "en": "shading and louvres", "he": "הצללה ורפפות", "ar": "التظليل والشيش",
        "ru": "солнцезащита и жалюзи", "it": "frangisole e lamelle",
        "es": "protección solar y lamas",
    },
    "family.mesh": {
        "en": "insect screens", "he": "רשתות", "ar": "شبكات", "ru": "москитные сетки",
        "it": "zanzariere", "es": "mosquiteras",
    },

    # -- materials ---------------------------------------------------------- #
    "material.aluminium": {
        "en": "aluminium", "he": "אלומיניום", "ar": "ألومنيوم", "ru": "алюминий",
        "it": "alluminio", "es": "aluminio",
    },
    "material.polyamide": {
        "en": "polyamide", "he": "פוליאמיד", "ar": "بولي أميد", "ru": "полиамид",
        "it": "poliammide", "es": "poliamida",
    },
    "material.steel": {
        "en": "steel", "he": "פלדה", "ar": "فولاذ", "ru": "сталь",
        "it": "acciaio", "es": "acero",
    },
    "material.epdm": {
        "en": "EPDM", "he": "EPDM", "ar": "EPDM", "ru": "EPDM",
        "it": "EPDM", "es": "EPDM",
    },

    # -- drawings ----------------------------------------------------------- #
    "drawing.elevation": {
        "en": "elevation", "he": "חזית", "ar": "واجهة", "ru": "фасад",
        "it": "prospetto", "es": "alzado",
    },
    "drawing.section": {
        "en": "section", "he": "חתך", "ar": "مقطع", "ru": "разрез",
        "it": "sezione", "es": "sección",
    },
    "drawing.head": {
        "en": "head detail", "he": "חתך משקוף עליון", "ar": "تفصيل العتب",
        "ru": "узел верха", "it": "nodo superiore", "es": "detalle de dintel",
    },
    "drawing.sill": {
        "en": "sill detail", "he": "חתך סף תחתון", "ar": "تفصيل العتبة",
        "ru": "узел низа", "it": "nodo inferiore", "es": "detalle de alféizar",
    },
    "drawing.jamb": {
        "en": "jamb detail", "he": "חתך משקוף צד", "ar": "تفصيل الجانب",
        "ru": "узел примыкания", "it": "nodo laterale", "es": "detalle de jamba",
    },
    "drawing.mullion": {
        "en": "mullion detail", "he": "חתך עמוד", "ar": "تفصيل القائم",
        "ru": "узел стойки", "it": "nodo montante", "es": "detalle de montante",
    },
    "drawing.transom": {
        "en": "transom detail", "he": "חתך קורה", "ar": "تفصيل العارضة",
        "ru": "узел ригеля", "it": "nodo traverso", "es": "detalle de travesaño",
    },
    "drawing.project": {
        "en": "Project", "he": "פרויקט", "ar": "المشروع", "ru": "Объект",
        "it": "Commessa", "es": "Obra",
    },
    "drawing.client": {
        "en": "Client", "he": "לקוח", "ar": "العميل", "ru": "Заказчик",
        "it": "Cliente", "es": "Cliente",
    },
    "drawing.title": {
        "en": "Title", "he": "תוכן", "ar": "المحتوى", "ru": "Наименование",
        "it": "Contenuto", "es": "Contenido",
    },
    "drawing.number": {
        "en": "Drawing", "he": "מס' שרטוט", "ar": "رقم الرسم", "ru": "Чертёж",
        "it": "Disegno", "es": "Plano",
    },
    "drawing.scale": {
        "en": "Scale", "he": "קנ\"מ", "ar": "المقياس", "ru": "Масштаб",
        "it": "Scala", "es": "Escala",
    },
    "drawing.sheet": {
        "en": "Sheet", "he": "גיליון", "ar": "الورقة", "ru": "Формат",
        "it": "Foglio", "es": "Hoja",
    },
    "drawing.revision": {
        "en": "Rev", "he": "מהדורה", "ar": "المراجعة", "ru": "Изм.",
        "it": "Rev", "es": "Rev",
    },
    "drawing.date": {
        "en": "Date", "he": "תאריך", "ar": "التاريخ", "ru": "Дата",
        "it": "Data", "es": "Fecha",
    },
    "drawing.drawn": {
        "en": "Drawn", "he": "שורטט", "ar": "رسم", "ru": "Чертил",
        "it": "Disegnato", "es": "Dibujado",
    },
    "drawing.checked": {
        "en": "Checked", "he": "נבדק", "ar": "روجع", "ru": "Проверил",
        "it": "Verificato", "es": "Revisado",
    },
    "drawing.legend": {
        "en": "legend", "he": "מקרא", "ar": "مفتاح الرموز", "ru": "условные обозначения",
        "it": "legenda", "es": "leyenda",
    },
    "drawing.legend_note": {
        "en": "the lines meet at the hinged edge",
        "he": "הקווים נפגשים בצד הצירים",
        "ar": "تلتقي الخطوط عند جهة المفصلات",
        "ru": "линии сходятся на стороне петель",
        "it": "le linee si incontrano sul lato cerniere",
        "es": "las líneas se encuentran en el lado de las bisagras",
    },
    "drawing.opens_outward": {
        "en": "opens outward, towards the reader",
        "he": "פתיחה החוצה",
        "ar": "يفتح إلى الخارج",
        "ru": "открывается наружу",
        "it": "apertura verso l'esterno",
        "es": "apertura hacia el exterior",
    },
    "drawing.opens_inward": {
        "en": "opens into the room",
        "he": "פתיחה פנימה",
        "ar": "يفتح إلى الداخل",
        "ru": "открывается внутрь",
        "it": "apertura verso l'interno",
        "es": "apertura hacia el interior",
    },
    "drawing.not_for_construction": {
        "en": "NOT FOR CONSTRUCTION — the systems shown have not had their supplier figures loaded",
        "he": "לא לביצוע — נתוני היצרן לסדרות שבשרטוט לא נטענו",
        "ar": "ليس للتنفيذ — لم تُحمَّل بيانات المُصنِّع لهذه السلاسل",
        "ru": "НЕ ДЛЯ ПРОИЗВОДСТВА — данные производителя по этим системам не загружены",
        "it": "NON PER LA COSTRUZIONE — i dati del fornitore per questi sistemi non sono stati caricati",
        "es": "NO APTO PARA CONSTRUCCIÓN — no se han cargado los datos del proveedor para estos sistemas",
    },
    "drawing.schematic_profile": {
        "en": "the profile is drawn schematically; no supplier section was imported",
        "he": "הפרופיל משורטט סכמטית — לא יובא חתך מהיצרן",
        "ar": "القطاع مرسوم تخطيطياً — لم يُستورد مقطع من المُصنِّع",
        "ru": "профиль показан схематично — сечение производителя не импортировано",
        "it": "il profilo è disegnato schematicamente; nessuna sezione del fornitore è stata importata",
        "es": "el perfil se dibuja esquemáticamente; no se importó ninguna sección del proveedor",
    },

    # -- feasibility -------------------------------------------------------- #
    "severity.blocker": {
        "en": "cannot be made", "he": "לא ניתן לייצור", "ar": "غير قابل للتصنيع",
        "ru": "изготовить нельзя", "it": "non producibile", "es": "no fabricable",
    },
    "severity.warning": {
        "en": "warning", "he": "אזהרה", "ar": "تحذير", "ru": "предупреждение",
        "it": "avvertenza", "es": "advertencia",
    },
    "severity.note": {
        "en": "note", "he": "לתשומת לב", "ar": "ملاحظة", "ru": "к сведению",
        "it": "nota", "es": "nota",
    },
    "check.buildable": {
        "en": "buildable as drawn", "he": "ניתן לייצור", "ar": "قابل للتصنيع كما هو",
        "ru": "изготовить можно", "it": "producibile come disegnato",
        "es": "fabricable tal como está",
    },

    # -- provenance --------------------------------------------------------- #
    "provenance.confirmed": {
        "en": "from the supplier's catalogue", "he": "מאושר מקטלוג היצרן",
        "ar": "من كتالوج المُصنِّع", "ru": "из каталога производителя",
        "it": "dal catalogo del fornitore", "es": "del catálogo del proveedor",
    },
    "provenance.typical": {
        "en": "a typical value, not the supplier's", "he": "ערך אופייני — לא מהיצרן",
        "ar": "قيمة نموذجية — ليست من المُصنِّع", "ru": "типовое значение, не от производителя",
        "it": "valore tipico, non del fornitore", "es": "valor típico, no del proveedor",
    },
    "provenance.unknown": {
        "en": "missing — load the catalogue", "he": "חסר — יש לטעון קטלוג",
        "ar": "مفقود — حمِّل الكتالوج", "ru": "отсутствует — загрузите каталог",
        "it": "mancante — caricare il catalogo", "es": "falta — cargue el catálogo",
    },

    # -- the phone ---------------------------------------------------------- #
    "mobile.pair_title": {
        "en": "Pair this device", "he": "חיבור המכשיר", "ar": "اقتران الجهاز",
        "ru": "Подключение устройства", "it": "Abbina il dispositivo",
        "es": "Vincular el dispositivo",
    },
    "mobile.pair_help": {
        "en": "On the office computer: System ← Phones ← pairing code. It lasts five minutes and works once.",
        "he": "במחשב במשרד: מערכת ← טלפונים ← קוד חיבור. הקוד תקף חמש דקות ולשימוש חד־פעמי.",
        "ar": "على حاسوب المكتب: النظام ← الهواتف ← رمز الاقتران. صالح خمس دقائق ولمرة واحدة.",
        "ru": "На офисном компьютере: Система ← Телефоны ← код. Действует пять минут, один раз.",
        "it": "Sul computer dell'ufficio: Sistema ← Telefoni ← codice. Vale cinque minuti, una volta.",
        "es": "En el ordenador de la oficina: Sistema ← Teléfonos ← código. Vale cinco minutos, una vez.",
    },
    "mobile.pair_code": {
        "en": "Pairing code", "he": "קוד חיבור", "ar": "رمز الاقتران",
        "ru": "Код подключения", "it": "Codice di abbinamento", "es": "Código de vinculación",
    },
    "mobile.device_name": {
        "en": "Device name (whose it is)", "he": "שם המכשיר (למי הוא שייך)",
        "ar": "اسم الجهاز (لمن يعود)", "ru": "Название устройства (чьё оно)",
        "it": "Nome del dispositivo (di chi è)", "es": "Nombre del dispositivo (de quién es)",
    },
    "mobile.pair_button": {
        "en": "Pair", "he": "חבר", "ar": "اقترن", "ru": "Подключить",
        "it": "Abbina", "es": "Vincular",
    },
    "mobile.tab_jobs": {
        "en": "Work", "he": "עבודה", "ar": "العمل", "ru": "Работа",
        "it": "Lavoro", "es": "Trabajo",
    },
    "mobile.tab_measure": {
        "en": "Measure", "he": "מדידה", "ar": "قياس", "ru": "Замер",
        "it": "Misura", "es": "Medición",
    },
    "mobile.tab_check": {
        "en": "Check", "he": "בדיקה", "ar": "فحص", "ru": "Проверка",
        "it": "Verifica", "es": "Comprobar",
    },
    "mobile.tab_drawings": {
        "en": "Drawings", "he": "שרטוטים", "ar": "الرسومات", "ru": "Чертежи",
        "it": "Disegni", "es": "Planos",
    },
    "mobile.scan": {
        "en": "Scan", "he": "סריקה", "ar": "مسح", "ru": "Сканирование",
        "it": "Scansione", "es": "Escaneo",
    },
    "mobile.scan_field": {
        "en": "Barcode or item number", "he": "ברקוד או מספר פריט",
        "ar": "الباركود أو رقم البند", "ru": "Штрихкод или номер позиции",
        "it": "Codice a barre o numero articolo", "es": "Código de barras o número de artículo",
    },
    "mobile.stage": {
        "en": "Stage", "he": "שלב", "ar": "المرحلة", "ru": "Этап",
        "it": "Fase", "es": "Etapa",
    },
    "mobile.set_stage": {
        "en": "Update stage", "he": "עדכן שלב", "ar": "حدِّث المرحلة",
        "ru": "Обновить этап", "it": "Aggiorna fase", "es": "Actualizar etapa",
    },
    "mobile.work_order": {
        "en": "Work order", "he": "פקודת עבודה", "ar": "أمر عمل",
        "ru": "Наряд-заказ", "it": "Ordine di lavoro", "es": "Orden de trabajo",
    },
    "mobile.no_work_order": {
        "en": "No work order has been loaded in the office.",
        "he": "אין פקודת עבודה בייצור — שחררו אחת ב״רצפת ייצור״ במחשב במשרד.",
        "ar": "لم يُحمَّل أمر عمل في المكتب.",
        "ru": "В офисе не загружен наряд-заказ.",
        "it": "Nessun ordine di lavoro caricato in ufficio.",
        "es": "No se ha cargado ninguna orden de trabajo en la oficina.",
    },
    "mobile.site_measure": {
        "en": "Site measurement", "he": "מדידה באתר", "ar": "قياس في الموقع",
        "ru": "Замер на объекте", "it": "Rilievo in cantiere", "es": "Medición en obra",
    },
    "mobile.measure_help": {
        "en": "Three widths and three heights — an opening is not a rectangle.",
        "he": "שלוש מידות לרוחב ושלוש לגובה — פתח אינו מלבן.",
        "ar": "ثلاثة عروض وثلاثة ارتفاعات — الفتحة ليست مستطيلاً.",
        "ru": "Три ширины и три высоты — проём не прямоугольник.",
        "it": "Tre larghezze e tre altezze — un vano non è un rettangolo.",
        "es": "Tres anchos y tres altos — un hueco no es un rectángulo.",
    },
    "mobile.tab_site": {
        "en": "On site", "he": "באתר", "ar": "في الموقع",
        "ru": "На объекте", "it": "In cantiere", "es": "En obra",
    },
    "mobile.photos": {
        "en": "Photos into the job", "he": "צילומים לתיק הפרויקט", "ar": "صور إلى ملف المشروع",
        "ru": "Фото в дело объекта", "it": "Foto nella commessa", "es": "Fotos a la obra",
    },
    "mobile.photos_help": {
        "en": "Each photo goes straight into the job's files in the office.", "he": "כל צילום נכנס ישר לתיק הפרויקט במשרד.", "ar": "كل صورة تدخل مباشرة إلى ملف المشروع في المكتب.",
        "ru": "Каждое фото сразу попадает в дело объекта в офисе.", "it": "Ogni foto entra subito nei file della commessa in ufficio.", "es": "Cada foto entra directamente en los archivos de la obra en la oficina.",
    },
    "mobile.photo_kind": {
        "en": "What it shows", "he": "מה בצילום", "ar": "ماذا تُظهر",
        "ru": "Что на фото", "it": "Cosa mostra", "es": "Qué muestra",
    },
    "mobile.kind_progress": {
        "en": "Work in progress", "he": "התקדמות העבודה", "ar": "تقدّم العمل",
        "ru": "Ход работ", "it": "Avanzamento lavori", "es": "Avance de obra",
    },
    "mobile.kind_opening": {
        "en": "The opening", "he": "הפתח", "ar": "الفتحة",
        "ru": "Проём", "it": "Il vano", "es": "El hueco",
    },
    "mobile.kind_before": {
        "en": "Before the work", "he": "לפני העבודה", "ar": "قبل العمل",
        "ru": "До работ", "it": "Prima dei lavori", "es": "Antes de la obra",
    },
    "mobile.kind_after": {
        "en": "After the work", "he": "אחרי העבודה", "ar": "بعد العمل",
        "ru": "После работ", "it": "Dopo i lavori", "es": "Después de la obra",
    },
    "mobile.kind_defect": {
        "en": "A defect", "he": "ליקוי", "ar": "عيب",
        "ru": "Дефект", "it": "Un difetto", "es": "Un defecto",
    },
    "mobile.caption": {
        "en": "A word about it (optional)", "he": "מילה על הצילום (לא חובה)", "ar": "كلمة عن الصورة (اختياري)",
        "ru": "Пара слов (необязательно)", "it": "Una parola (facoltativo)", "es": "Una palabra (opcional)",
    },
    "mobile.take_photo": {
        "en": "Take a photo", "he": "צילום", "ar": "التقاط صورة",
        "ru": "Сфотографировать", "it": "Scatta una foto", "es": "Hacer una foto",
    },
    "mobile.photo_sending": {
        "en": "Sending…", "he": "שולח…", "ar": "جارٍ الإرسال…",
        "ru": "Отправка…", "it": "Invio…", "es": "Enviando…",
    },
    "mobile.photo_saved": {
        "en": "Saved in the job", "he": "נשמר בתיק הפרויקט", "ar": "حُفظت في ملف المشروع",
        "ru": "Сохранено в деле", "it": "Salvata nella commessa", "es": "Guardada en la obra",
    },
    "mobile.photo_offline": {
        "en": "No signal — the photo is still in the phone's gallery; send it again when there is signal.", "he": "אין קליטה — הצילום נשאר בגלריה של הטלפון. שלחו שוב כשיש קליטה.", "ar": "لا توجد إشارة — الصورة ما زالت في معرض الهاتف؛ أرسلها مجدداً عند توفر الإشارة.",
        "ru": "Нет связи — фото осталось в галерее телефона; отправьте снова, когда появится связь.", "it": "Nessun segnale — la foto resta nella galleria del telefono; inviala di nuovo quando c'è segnale.", "es": "Sin señal — la foto sigue en la galería del teléfono; envíala de nuevo cuando haya señal.",
    },
    "mobile.handover": {
        "en": "Handover", "he": "מסירה", "ar": "التسليم",
        "ru": "Сдача работ", "it": "Consegna", "es": "Entrega",
    },
    "mobile.handover_help": {
        "en": "The customer signs that the work was handed over. Anything left to fix is written now, one line each.", "he": "הלקוח חותם שהעבודה נמסרה. מה שנשאר לתקן נכתב עכשיו, שורה לכל דבר.", "ar": "يوقّع العميل على استلام العمل. ما بقي للإصلاح يُكتب الآن، سطر لكل أمر.",
        "ru": "Клиент подписывает приёмку. Что осталось исправить — записывается сейчас, по строке на пункт.", "it": "Il cliente firma la consegna. Ciò che resta da sistemare si scrive ora, una riga per voce.", "es": "El cliente firma la entrega. Lo que quede por arreglar se escribe ahora, una línea por cosa.",
    },
    "mobile.signed_by": {
        "en": "Received by", "he": "שם המקבל", "ar": "اسم المستلم",
        "ru": "Кто принял", "it": "Ricevuto da", "es": "Recibido por",
    },
    "mobile.snags_now": {
        "en": "Left to fix (one per line)", "he": "נשאר לתקן (שורה לכל דבר)", "ar": "بقي للإصلاح (سطر لكل أمر)",
        "ru": "Осталось исправить (по строке)", "it": "Da sistemare (una per riga)", "es": "Por arreglar (una por línea)",
    },
    "mobile.sign_here": {
        "en": "Sign here", "he": "חתימה כאן", "ar": "وقّع هنا",
        "ru": "Подпись здесь", "it": "Firma qui", "es": "Firme aquí",
    },
    "mobile.clear_signature": {
        "en": "Clear", "he": "ניקוי", "ar": "مسح",
        "ru": "Стереть", "it": "Cancella", "es": "Borrar",
    },
    "mobile.sign_and_hand_over": {
        "en": "Signed — hand over", "he": "נחתם — מסירה", "ar": "تم التوقيع — تسليم",
        "ru": "Подписано — сдать", "it": "Firmato — consegna", "es": "Firmado — entregar",
    },
    "mobile.need_signature": {
        "en": "The customer has not signed yet", "he": "הלקוח עוד לא חתם", "ar": "لم يوقّع العميل بعد",
        "ru": "Клиент ещё не подписал", "it": "Il cliente non ha ancora firmato", "es": "El cliente aún no ha firmado",
    },
    "mobile.photo_queued": {
        "en": "No signal — the photo is kept on the phone and will go by itself when there is signal.",
        "he": "אין קליטה — הצילום נשמר בטלפון וישלח לבד כשתחזור קליטה.",
        "ar": "لا توجد إشارة — حُفظت الصورة في الهاتف وستُرسل تلقائياً عند عودة الإشارة.",
        "ru": "Нет связи — фото сохранено в телефоне и уйдёт само, когда появится связь.",
        "it": "Nessun segnale — la foto resta nel telefono e partirà da sola quando c'è segnale.",
        "es": "Sin señal — la foto queda en el teléfono y se enviará sola cuando haya señal.",
    },
    "mobile.photos_waiting": {
        "en": "{n} photos waiting for signal", "he": "⁦{n}⁩ צילומים מחכים לקליטה",
        "ar": "{n} صور تنتظر الإشارة", "ru": "{n} фото ждут связи",
        "it": "{n} foto in attesa di segnale", "es": "{n} fotos esperando señal",
    },
    "mobile.handover_offline": {
        "en": "No signal — the handover was NOT sent. Try again while the customer is here.",
        "he": "אין קליטה — המסירה לא נשלחה. נסו שוב כל עוד הלקוח כאן.",
        "ar": "لا توجد إشارة — لم يُرسل التسليم. حاول مجدداً ما دام العميل هنا.",
        "ru": "Нет связи — сдача НЕ отправлена. Повторите, пока клиент рядом.",
        "it": "Nessun segnale — la consegna NON è stata inviata. Riprova finché il cliente è qui.",
        "es": "Sin señal — la entrega NO se envió. Inténtalo de nuevo mientras el cliente esté aquí.",
    },
    "mobile.handed_over": {
        "en": "Handed over and signed", "he": "נמסר ונחתם", "ar": "تم التسليم والتوقيع",
        "ru": "Сдано и подписано", "it": "Consegnato e firmato", "es": "Entregado y firmado",
    },
    "mobile.approve_quote": {
        "en": "Customer approves the quote", "he": "אישור הצעת מחיר", "ar": "موافقة العميل على العرض",
        "ru": "Клиент одобряет смету", "it": "Il cliente approva il preventivo", "es": "El cliente aprueba el presupuesto",
    },
    "mobile.approve_help": {
        "en": "Show the customer the version they were sent; they approve it and sign here — the job is won on the spot.", "he": "מציגים ללקוח את הגרסה שנשלחה אליו; הוא מאשר וחותם כאן — והפרויקט נסגר במקום.", "ar": "اعرض على العميل النسخة التي أُرسلت إليه؛ يوافق ويوقّع هنا — ويُغلق المشروع فوراً.",
        "ru": "Покажите клиенту отправленную ему версию; он одобряет и подписывает здесь — заказ закрыт на месте.", "it": "Mostra al cliente la versione inviata; la approva e firma qui — la commessa è chiusa sul posto.", "es": "Muestra al cliente la versión enviada; la aprueba y firma aquí — la obra se cierra en el acto.",
    },
    "mobile.show_quote": {
        "en": "Show the quote", "he": "הצגת ההצעה", "ar": "عرض العرض",
        "ru": "Показать смету", "it": "Mostra il preventivo", "es": "Mostrar el presupuesto",
    },
    "mobile.version": {
        "en": "version", "he": "גרסה", "ar": "نسخة",
        "ru": "версия", "it": "versione", "es": "versión",
    },
    "mobile.total_with_vat": {
        "en": "Total incl. VAT", "he": "סה״כ כולל מע״מ", "ar": "المجموع شامل الضريبة",
        "ru": "Итого с НДС", "it": "Totale IVA inclusa", "es": "Total con IVA",
    },
    "mobile.customer_approves": {
        "en": "The customer approves — signed", "he": "הלקוח מאשר — נחתם", "ar": "العميل يوافق — تم التوقيع",
        "ru": "Клиент одобряет — подписано", "it": "Il cliente approva — firmato", "es": "El cliente aprueba — firmado",
    },
    "mobile.quote_approved": {
        "en": "Approved and signed — the job is won", "he": "אושר ונחתם — הפרויקט נסגר", "ar": "تمت الموافقة والتوقيع — أُغلق المشروع",
        "ru": "Одобрено и подписано — заказ получен", "it": "Approvato e firmato — commessa acquisita", "es": "Aprobado y firmado — obra ganada",
    },
    "mobile.project": {
        "en": "Job", "he": "פרויקט", "ar": "المشروع",
        "ru": "Объект", "it": "Commessa", "es": "Obra",
    },
    "mobile.need_project": {
        "en": "Choose the job this opening belongs to",
        "he": "בחרו לאיזה פרויקט שייך הפתח",
        "ar": "اختر المشروع الذي تتبع له الفتحة",
        "ru": "Выберите объект, к которому относится проём",
        "it": "Scegli la commessa a cui appartiene il vano",
        "es": "Elige la obra a la que pertenece el hueco",
    },
    "mobile.opening_ref": {
        "en": "Opening mark", "he": "סימון הפתח", "ar": "رمز الفتحة",
        "ru": "Обозначение проёма", "it": "Sigla del vano", "es": "Marca del hueco",
    },
    "mobile.widths": {
        "en": "Width — top / middle / bottom", "he": "רוחב — עליון / אמצע / תחתון",
        "ar": "العرض — أعلى / وسط / أسفل", "ru": "Ширина — верх / середина / низ",
        "it": "Larghezza — alto / centro / basso", "es": "Ancho — arriba / centro / abajo",
    },
    "mobile.heights": {
        "en": "Height — right / middle / left", "he": "גובה — ימין / אמצע / שמאל",
        "ar": "الارتفاع — يمين / وسط / يسار", "ru": "Высота — справа / посередине / слева",
        "it": "Altezza — destra / centro / sinistra", "es": "Alto — derecha / centro / izquierda",
    },
    "mobile.diagonals": {
        "en": "Diagonals (optional)", "he": "אלכסונים (לא חובה)",
        "ar": "الأقطار (اختياري)", "ru": "Диагонали (необязательно)",
        "it": "Diagonali (facoltativo)", "es": "Diagonales (opcional)",
    },
    "mobile.note": {
        "en": "Note", "he": "הערה", "ar": "ملاحظة", "ru": "Примечание",
        "it": "Nota", "es": "Nota",
    },
    "mobile.send": {
        "en": "Save and send to the office", "he": "שמור ושלח למשרד",
        "ar": "احفظ وأرسل إلى المكتب", "ru": "Сохранить и отправить в офис",
        "it": "Salva e invia all'ufficio", "es": "Guardar y enviar a la oficina",
    },
    "mobile.recent": {
        "en": "Recent measurements", "he": "מדידות אחרונות", "ar": "آخر القياسات",
        "ru": "Последние замеры", "it": "Ultimi rilievi", "es": "Últimas mediciones",
    },
    "mobile.none_yet": {
        "en": "Nothing measured yet.", "he": "אין עדיין מדידות.",
        "ar": "لا توجد قياسات بعد.", "ru": "Замеров пока нет.",
        "it": "Nessun rilievo finora.", "es": "Todavía no hay mediciones.",
    },
    "mobile.saved_offline": {
        "en": "No signal — saved on this phone. It will be sent by itself when the network is back.",
        "he": "אין קליטה — נשמר בטלפון. יישלח לבד כשהרשת תחזור.",
        "ar": "لا توجد إشارة — حُفظ على الهاتف. سيُرسل تلقائياً عند عودة الشبكة.",
        "ru": "Нет связи — сохранено на телефоне. Отправится само, когда появится сеть.",
        "it": "Nessun segnale — salvato sul telefono. Verrà inviato da solo quando torna la rete.",
        "es": "Sin señal — guardado en el teléfono. Se enviará solo cuando vuelva la red.",
    },
    "mobile.outbox": {
        "en": "Measurements kept on this phone, waiting for the network: {n}. They are sent by themselves — even if the page is closed and opened again later.",
        "he": "מדידות שמורות בטלפון וממתינות לרשת: {n}. הן יישלחו לבד — גם אם הדף ייסגר וייפתח שוב אחר כך.",
        "ar": "قياسات محفوظة على الهاتف بانتظار الشبكة: {n}. تُرسل تلقائياً — حتى لو أُغلقت الصفحة وفُتحت لاحقاً.",
        "ru": "Замеры на телефоне ждут сети: {n}. Отправятся сами — даже если страницу закрыть и открыть позже.",
        "it": "Rilievi salvati sul telefono in attesa di rete: {n}. Vengono inviati da soli — anche se la pagina viene chiusa e riaperta.",
        "es": "Mediciones guardadas en el teléfono esperando red: {n}. Se envían solas — aunque la página se cierre y se vuelva a abrir.",
    },
    "mobile.waiting_network": {
        "en": "Waiting for network", "he": "ממתין לרשת", "ar": "بانتظار الشبكة",
        "ru": "Ждёт сети", "it": "In attesa di rete", "es": "Esperando red",
    },
    "mobile.sent_from_phone": {
        "en": "Measurements that waited on the phone were sent: {n}.",
        "he": "נשלחו מדידות שחיכו בטלפון: {n}.",
        "ar": "أُرسلت القياسات التي انتظرت على الهاتف: {n}.",
        "ru": "Отправлены замеры, ждавшие на телефоне: {n}.",
        "it": "Inviati i rilievi rimasti sul telefono: {n}.",
        "es": "Se enviaron las mediciones que esperaban en el teléfono: {n}.",
    },
    "mobile.not_saved": {
        "en": "No signal, and the phone could not store it either — it is still in the form. Write it down.",
        "he": "אין קליטה, וגם הטלפון לא הצליח לשמור — המידות עדיין בטופס. רשמו אותן בצד.",
        "ar": "لا توجد إشارة، ولم يتمكن الهاتف من الحفظ أيضاً — القياسات ما زالت في النموذج. دوّنها.",
        "ru": "Нет связи, и телефон не смог сохранить — размеры остались в форме. Запишите их.",
        "it": "Nessun segnale e il telefono non ha potuto salvare — le misure sono ancora nel modulo. Annotatele.",
        "es": "Sin señal y el teléfono tampoco pudo guardarlo — las medidas siguen en el formulario. Anótelas.",
    },
    "mobile.retry": {
        "en": "Send again", "he": "שליחה שוב", "ar": "إرسال مجدداً", "ru": "Отправить снова",
        "it": "Invia di nuovo", "es": "Enviar de nuevo",
    },
    "mobile.drop": {
        "en": "Delete", "he": "מחיקה", "ar": "حذف", "ru": "Удалить", "it": "Elimina", "es": "Eliminar",
    },
    "mobile.drop_confirm": {
        "en": "Delete this measurement from the phone? It never reached the office.",
        "he": "למחוק את המדידה מהטלפון? היא לא הגיעה למשרד.",
        "ar": "حذف هذا القياس من الهاتف؟ لم يصل إلى المكتب.",
        "ru": "Удалить этот замер с телефона? Он не дошёл до офиса.",
        "it": "Eliminare questo rilievo dal telefono? Non è mai arrivato in ufficio.",
        "es": "¿Eliminar esta medición del teléfono? Nunca llegó a la oficina.",
    },
    "mobile.not_accepted": {
        "en": "Not accepted by the office", "he": "לא התקבלה במשרד", "ar": "لم يقبلها المكتب",
        "ru": "Не принят офисом", "it": "Non accettato dall'ufficio", "es": "No aceptada por la oficina",
    },
    "mobile.feasibility": {
        "en": "Feasibility check", "he": "בדיקת ישימות", "ar": "فحص القابلية للتنفيذ",
        "ru": "Проверка выполнимости", "it": "Verifica di fattibilità",
        "es": "Comprobación de viabilidad",
    },
    "mobile.check_help": {
        "en": "Structural opening sizes, before the installation joint.",
        "he": "מידות הפתח נטו, לפני קיזוז ההתקנה.",
        "ar": "أبعاد الفتحة الإنشائية، قبل فاصل التركيب.",
        "ru": "Размеры проёма до монтажного зазора.",
        "it": "Misure del vano strutturale, prima del giunto di posa.",
        "es": "Medidas del hueco estructural, antes de la junta de montaje.",
    },
    "mobile.width": {
        "en": "Width", "he": "רוחב", "ar": "العرض", "ru": "Ширина",
        "it": "Larghezza", "es": "Ancho",
    },
    "mobile.height": {
        "en": "Height", "he": "גובה", "ar": "الارتفاع", "ru": "Высота",
        "it": "Altezza", "es": "Alto",
    },
    "mobile.opening_type": {
        "en": "Opening type", "he": "סוג פתיחה", "ar": "نوع الفتح",
        "ru": "Тип открывания", "it": "Tipo di apertura", "es": "Tipo de apertura",
    },
    "mobile.sill_height": {
        "en": "Sill height above the floor", "he": "גובה הסף מהרצפה",
        "ar": "ارتفاع العتبة عن الأرضية", "ru": "Высота порога от пола",
        "it": "Altezza della soglia dal pavimento", "es": "Altura del alféizar sobre el suelo",
    },
    "mobile.check_button": {
        "en": "Check", "he": "בדוק", "ar": "افحص", "ru": "Проверить",
        "it": "Verifica", "es": "Comprobar",
    },
    "mobile.drawings": {
        "en": "Drawings", "he": "שרטוטים", "ar": "الرسومات", "ru": "Чертежи",
        "it": "Disegni", "es": "Planos",
    },
    "mobile.no_elements": {
        "en": "No elements are loaded in the office.", "he": "אין אלמנטים טעונים במשרד.",
        "ar": "لا توجد عناصر محمَّلة في المكتب.", "ru": "В офисе не загружены изделия.",
        "it": "Nessun serramento caricato in ufficio.", "es": "No hay elementos cargados en la oficina.",
    },
    "mobile.session_expired": {
        "en": "The session has ended. Pair the device again.",
        "he": "החיבור פג. יש לחבר את המכשיר מחדש.",
        "ar": "انتهت الجلسة. أعد اقتران الجهاز.",
        "ru": "Сеанс завершён. Подключите устройство заново.",
        "it": "La sessione è terminata. Abbina di nuovo il dispositivo.",
        "es": "La sesión ha terminado. Vuelva a vincular el dispositivo.",
    },
    "mobile.not_paired": {
        "en": "This device is not paired", "he": "המכשיר אינו מחובר",
        "ar": "الجهاز غير مقترن", "ru": "Устройство не подключено",
        "it": "Il dispositivo non è abbinato", "es": "El dispositivo no está vinculado",
    },
    "mobile.no_permission": {
        "en": "This device is not allowed to do that", "he": "למכשיר הזה אין הרשאה לכך",
        "ar": "هذا الجهاز غير مخوَّل بذلك", "ru": "Устройству это не разрешено",
        "it": "Il dispositivo non ha questo permesso", "es": "Este dispositivo no tiene permiso",
    },
    "mobile.office_decision": {
        "en": "That stage is set in the office, not on the phone",
        "he": "השלב הזה נקבע במשרד, לא מהטלפון",
        "ar": "تُحدَّد هذه المرحلة في المكتب وليس من الهاتف",
        "ru": "Этот этап устанавливается в офисе, а не с телефона",
        "it": "Questa fase si imposta in ufficio, non dal telefono",
        "es": "Esa etapa se fija en la oficina, no desde el teléfono",
    },
    "mobile.need_sizes": {
        "en": "A width and a height are both needed", "he": "צריך רוחב וגובה",
        "ar": "مطلوب عرض وارتفاع", "ru": "Нужны и ширина, и высота",
        "it": "Servono larghezza e altezza", "es": "Hacen falta ancho y alto",
    },
    "mobile.need_reference": {
        "en": "The opening needs a mark", "he": "צריך סימון לפתח",
        "ar": "الفتحة تحتاج إلى رمز", "ru": "Проёму нужно обозначение",
        "it": "Il vano ha bisogno di una sigla", "es": "El hueco necesita una marca",
    },
    "mobile.top": {
        "en": "top", "he": "עליון", "ar": "أعلى", "ru": "верх",
        "it": "alto", "es": "arriba",
    },
    "mobile.middle": {
        "en": "middle", "he": "אמצע", "ar": "وسط", "ru": "середина",
        "it": "centro", "es": "centro",
    },
    "mobile.bottom": {
        "en": "bottom", "he": "תחתון", "ar": "أسفل", "ru": "низ",
        "it": "basso", "es": "abajo",
    },
    "mobile.right": {
        "en": "right", "he": "ימין", "ar": "يمين", "ru": "справа",
        "it": "destra", "es": "derecha",
    },
    "mobile.left": {
        "en": "left", "he": "שמאל", "ar": "يسار", "ru": "слева",
        "it": "sinistra", "es": "izquierda",
    },
    "mobile.diagonal": {
        "en": "diagonal", "he": "אלכסון", "ar": "قطر", "ru": "диагональ",
        "it": "diagonale", "es": "diagonal",
    },
    "mobile.device_example": {
        "en": "e.g. Dadi's phone", "he": "לדוגמה: הטלפון של דאדי",
        "ar": "مثال: هاتف دادي", "ru": "например: телефон Дади",
        "it": "es. il telefono di Dadi", "es": "p. ej. el teléfono de Dadi",
    },
    "mobile.note_example": {
        "en": "storey, position, anything worth knowing",
        "he": "קומה, מיקום, מה שצריך לדעת",
        "ar": "الطابق، الموقع، أي شيء يستحق المعرفة",
        "ru": "этаж, место, всё, что важно знать",
        "it": "piano, posizione, quanto serve sapere",
        "es": "planta, ubicación, lo que convenga saber",
    },
    "mobile.language": {
        "en": "Language", "he": "שפה", "ar": "اللغة", "ru": "Язык",
        "it": "Lingua", "es": "Idioma",
    },

    # -- kinds of element ---------------------------------------------------- #
    "element.window": {
        "en": "window", "he": "חלון", "ar": "نافذة", "ru": "окно",
        "it": "finestra", "es": "ventana",
    },
    "element.door": {
        "en": "door", "he": "דלת", "ar": "باب", "ru": "дверь",
        "it": "porta", "es": "puerta",
    },
    "element.curtain_wall": {
        "en": "curtain wall", "he": "קיר מסך", "ar": "حائط ستائري",
        "ru": "навесной фасад", "it": "facciata continua", "es": "muro cortina",
    },
    "element.shopfront": {
        "en": "shopfront", "he": "חזית מסחרית", "ar": "واجهة تجارية",
        "ru": "витрина", "it": "vetrina", "es": "escaparate",
    },
    "element.skylight": {
        "en": "skylight", "he": "סקיילייט", "ar": "منور سقفي",
        "ru": "зенитный фонарь", "it": "lucernario", "es": "lucernario",
    },

    # -- quantities --------------------------------------------------------- #
    "unit.mm": {"en": "mm", "he": "מ\"מ", "ar": "مم", "ru": "мм", "it": "mm", "es": "mm"},
    "unit.m": {"en": "m", "he": "מ'", "ar": "م", "ru": "м", "it": "m", "es": "m"},
    "unit.m2": {"en": "m²", "he": "מ\"ר", "ar": "م²", "ru": "м²", "it": "m²", "es": "m²"},
    "unit.kg": {"en": "kg", "he": "ק\"ג", "ar": "كغ", "ru": "кг", "it": "kg", "es": "kg"},
    "unit.kg_per_m": {
        "en": "kg/m", "he": "ק\"ג/מ'", "ar": "كغ/م", "ru": "кг/м",
        "it": "kg/m", "es": "kg/m",
    },
    "unit.m2_per_m": {
        "en": "m²/m", "he": "מ\"ר/מ'", "ar": "م²/م", "ru": "м²/м",
        "it": "m²/m", "es": "m²/m",
    },
    "unit.pieces": {
        "en": "pcs", "he": "יח'", "ar": "قطعة", "ru": "шт.", "it": "pz", "es": "uds",
    },

    # -- quotations --------------------------------------------------------- #
    "quote.quotation": {
        "en": "Quotation", "he": "הצעת מחיר", "ar": "عرض سعر",
        "ru": "Коммерческое предложение", "it": "Preventivo", "es": "Presupuesto",
    },
    "quote.item": {
        "en": "Item", "he": "פריט", "ar": "بند", "ru": "Позиция",
        "it": "Voce", "es": "Partida",
    },
    "quote.description": {
        "en": "Description", "he": "תיאור", "ar": "الوصف", "ru": "Описание",
        "it": "Descrizione", "es": "Descripción",
    },
    "quote.quantity": {
        "en": "Qty", "he": "כמות", "ar": "الكمية", "ru": "Кол-во",
        "it": "Q.tà", "es": "Cant.",
    },
    "quote.unit_price": {
        "en": "Unit price", "he": "מחיר ליחידה", "ar": "سعر الوحدة",
        "ru": "Цена за единицу", "it": "Prezzo unitario", "es": "Precio unitario",
    },
    "quote.total": {
        "en": "Total", "he": "סה\"כ", "ar": "الإجمالي", "ru": "Итого",
        "it": "Totale", "es": "Total",
    },
    "quote.subtotal": {
        "en": "Subtotal", "he": "סכום ביניים", "ar": "المجموع الفرعي",
        "ru": "Промежуточный итог", "it": "Imponibile", "es": "Subtotal",
    },
    "quote.discount": {
        "en": "Discount", "he": "הנחה", "ar": "خصم", "ru": "Скидка",
        "it": "Sconto", "es": "Descuento",
    },
    "quote.vat": {
        "en": "VAT", "he": "מע\"מ", "ar": "ضريبة القيمة المضافة", "ru": "НДС",
        "it": "IVA", "es": "IVA",
    },
    "quote.grand_total": {
        "en": "Total due", "he": "סה\"כ לתשלום", "ar": "الإجمالي المستحق",
        "ru": "Всего к оплате", "it": "Totale da pagare", "es": "Total a pagar",
    },
    "quote.option": {
        "en": "Option", "he": "חלופה", "ar": "خيار", "ru": "Вариант",
        "it": "Opzione", "es": "Opción",
    },
    "quote.valid_until": {
        "en": "Valid until", "he": "בתוקף עד", "ar": "صالح حتى",
        "ru": "Действительно до", "it": "Valido fino al", "es": "Válido hasta",
    },
}


def language_codes() -> tuple[str, ...]:
    return tuple(language.value for language in Language)


def missing() -> dict[str, list[str]]:
    """Keys with no entry in some language, for the completeness test."""
    gaps: dict[str, list[str]] = {}
    for key, entries in MESSAGES.items():
        absent = [code for code in language_codes() if not entries.get(code)]
        if absent:
            gaps[key] = absent
    return gaps


__all__ = ["MESSAGES", "language_codes", "missing"]
