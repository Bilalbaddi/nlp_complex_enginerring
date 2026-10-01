"""Static definitions for the synthetic city: wards, departments, seasonality and injected effects.

The city ("Sahyadripur") and all people, phones and documents are fictional.
"""

from dataclasses import dataclass
from datetime import date

CITY = "Sahyadripur"
CENTER_LAT, CENTER_LON = 20.0000, 73.7900
GRID_COLS, GRID_ROWS = 5, 4
CELL_LON, CELL_LAT = 0.0125, 0.0113  # ~1.3 km x ~1.25 km per ward cell
DEFAULT_END_DATE = date(2026, 9, 30)  # exclusive upper bound of the generated period
IST = "+05:30"

CATEGORIES = [
    "water_supply", "drainage", "roads", "garbage", "streetlights",
    "encroachment", "public_health", "electricity", "parks", "noise",
]
# share of baseline complaint volume
CATEGORY_SHARE = [0.16, 0.10, 0.15, 0.18, 0.11, 0.05, 0.06, 0.09, 0.04, 0.06]


@dataclass(frozen=True)
class Dept:
    code: str
    name: str
    category: str
    sla_hours: int
    median_sla_fraction: float  # typical resolution time as a fraction of the SLA
    performance_factor: float  # >1 = slower than typical (underperforming department)


DEPARTMENTS = [
    Dept("WSD", "Water Supply Department", "water_supply", 48, 0.62, 1.00),
    Dept("DSD", "Drainage & Sewerage Department", "drainage", 72, 0.62, 1.35),
    Dept("RMD", "Roads & Maintenance Department", "roads", 120, 0.66, 1.25),
    Dept("SWM", "Solid Waste Management Department", "garbage", 24, 0.55, 0.85),
    Dept("SLD", "Street Lighting Department", "streetlights", 72, 0.55, 1.00),
    Dept("ERD", "Encroachment Removal Department", "encroachment", 168, 0.70, 1.50),
    Dept("PHD", "Public Health Department", "public_health", 48, 0.75, 1.00),
    Dept("ELD", "Electricity Department", "electricity", 24, 0.60, 0.90),
    Dept("PGD", "Parks & Gardens Department", "parks", 168, 0.80, 1.00),
    Dept("PCD", "Pollution Control Department", "noise", 48, 0.85, 1.10),
]

WARD_NAMES = [
    "Shivaji Nagar", "Gandhi Chowk", "Lakshmi Colony", "Nehru Park", "Ambedkar Nagar",
    "Tilak Road", "Sahyadri Heights", "Old City", "Market Yard", "Railway Colony",
    "College Road", "Ganesh Peth", "Kasturba Nagar", "Rani Laxmibai Nagar", "Industrial Estate",
    "Riverside", "Hanuman Wadi", "Sai Vihar", "Subhash Nagar", "Green Valley",
]
# ward code -> resolution slowdown factor (>1 underperforming, <1 well run)
WARD_PERFORMANCE = {"W05": 1.9, "W12": 1.7, "W17": 1.8, "W15": 1.4, "W03": 0.75, "W14": 0.80, "W20": 0.85}
# language mix tilt per ward: code -> (en, hinglish, hindi, marathi) weights; default below
DEFAULT_LANG_MIX = (0.58, 0.15, 0.12, 0.15)
WARD_LANG_MIX = {
    "W08": (0.35, 0.15, 0.10, 0.40), "W12": (0.30, 0.10, 0.10, 0.50), "W05": (0.40, 0.25, 0.25, 0.10),
    "W17": (0.35, 0.10, 0.10, 0.45), "W15": (0.45, 0.30, 0.20, 0.05), "W20": (0.80, 0.10, 0.05, 0.05),
}
LANGUAGES = ["en", "hinglish", "hi", "mr"]

# month multipliers (Jan..Dec) per category
SEASONALITY = {
    "water_supply": [1.0, 1.0, 1.2, 1.5, 1.7, 1.4, 0.9, 0.8, 0.9, 1.0, 1.0, 1.0],
    "drainage": [0.5, 0.5, 0.5, 0.6, 0.8, 2.8, 4.0, 4.0, 3.0, 1.2, 0.7, 0.5],
    "roads": [0.8, 0.8, 0.8, 0.8, 0.9, 1.4, 2.2, 2.3, 2.0, 1.6, 1.0, 0.8],
    "garbage": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.3, 1.3, 1.2, 1.1],
    "streetlights": [1.4, 1.2, 1.0, 0.9, 0.8, 0.8, 0.9, 0.9, 1.0, 1.1, 1.3, 1.5],
    "encroachment": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.1, 1.3, 1.4, 1.1],
    "public_health": [0.8, 0.8, 0.9, 1.0, 1.0, 1.3, 1.9, 2.2, 2.0, 1.6, 1.0, 0.8],
    "electricity": [0.9, 0.9, 1.1, 1.5, 1.8, 1.4, 1.0, 1.0, 1.0, 1.1, 0.9, 0.9],
    "parks": [1.0, 1.0, 1.1, 1.2, 1.1, 0.8, 0.7, 0.7, 0.9, 1.2, 1.3, 1.2],
    "noise": [1.0, 1.0, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 1.8, 2.2, 1.4, 1.0],
}
# resolution slows down in the monsoon for these categories
MONSOON_SLOWDOWN = {"drainage": 1.30, "roads": 1.25, "garbage": 1.10, "electricity": 1.15}
YEARLY_VOLUME_GROWTH = 0.08
ROADS_YEARLY_DETERIORATION = 0.15  # roads resolution time grows 15%/year (a planted trend)
HOTSPOT_PROB = {"drainage": 0.40, "garbage": 0.35, "roads": 0.35, "water_supply": 0.25, "streetlights": 0.20}
LABEL_NOISE = 0.03  # share of complaints whose category label differs from the true one
DUPLICATE_RATE = 0.03
CHANNELS = ["mobile_app", "web", "call_center", "whatsapp", "ward_office"]
CHANNEL_P = [0.38, 0.12, 0.22, 0.15, 0.13]
HOUR_P = [1, 1, 1, 1, 1, 2, 3, 5, 8, 10, 10, 9, 7, 6, 6, 6, 7, 8, 9, 8, 6, 4, 2, 1]


@dataclass(frozen=True)
class Burst:
    """An injected emerging-issue burst (extra complaints on top of the baseline)."""

    key: str
    label: str
    category: str
    wards: tuple[str, ...]
    start: date | None  # absolute start; None -> relative to the end date via `days_before_end`
    days_before_end: int | None
    duration_days: int
    count_at_50k: int  # complaints when the dataset has 50,000 rows; scaled linearly
    shape: str  # "flat" | "ramp" (rising, emerging) | "spike"
    spatial_focus: bool  # concentrate around one point inside the first ward
    second_category: str | None = None  # a share of the burst is filed under this category too


BURSTS = [
    Burst("contam", "Water contamination", "water_supply", ("W07",), date(2024, 5, 10), None, 21, 260, "spike", True, "public_health"),
    Burst("strike", "Garbage collection breakdown", "garbage", ("W12", "W13"), date(2025, 3, 3), None, 12, 300, "flat", False),
    Burst("lights", "Street light outage cluster", "streetlights", ("W02",), date(2025, 11, 15), None, 25, 220, "flat", True),
    Burst("flood", "Severe waterlogging", "drainage", ("W09", "W10"), date(2025, 7, 8), None, 6, 450, "spike", False),
    Burst("dengue", "Vector-borne disease surge", "public_health", ("W05", "W06"), None, 45, 45, 400, "ramp", False),
    Burst("potholes", "Ring road pothole surge", "roads", ("W15", "W16", "W20"), None, 30, 30, 350, "ramp", False),
    Burst("encroach", "Market gate encroachment", "encroachment", ("W08",), None, 20, 20, 120, "ramp", True),
]

# --------------------------------------------------------------------------- text
LANDMARK_POOL = [
    "Ganesh Mandir", "Zilla Parishad School", "Hanuman Temple", "Bus Stand", "Municipal School No. {k}",
    "Sai Baba Mandir", "Bhaji Market", "Government Hospital", "Masjid Chowk", "Gurudwara",
    "Water Tank", "Community Hall", "Police Chowki", "Petrol Pump", "Railway Gate", "Post Office", "Anganwadi",
]
STREET_POOL = [
    "MG Road", "Station Road", "Main Road", "Nehru Chowk Road", "Shivaji Marg", "Ambedkar Road",
    "Tilak Road", "Market Yard Road", "College Road", "Temple Road", "Lane No. {k}", "Gali No. {k}",
]

PROBLEMS: dict[str, dict[str, list[str]]] = {
    "en": {
        "water_supply": ["No water supply since morning", "Very low water pressure in the taps", "Dirty and foul smelling water coming from the tap", "Water tanker has not arrived as scheduled", "Water pipeline leaking on the road", "Irregular water timings, supply comes only for 20 minutes"],
        "drainage": ["Drain is overflowing onto the road", "Sewage water is entering our houses", "Manhole cover is broken and open", "Storm water drain blocked with garbage", "Severe waterlogging after the rain", "Sewer line choked and stinking"],
        "roads": ["Big pothole in the middle of the road", "Road is badly damaged and unsafe for two-wheelers", "Road caved in after digging by a utility company", "Speed breaker broken and dangerous", "Footpath tiles broken, pedestrians cannot walk", "Road surface washed away"],
        "garbage": ["Garbage has not been collected for several days", "Overflowing dustbin near the lane", "Waste dumped openly on vacant plot", "Garbage van did not come today", "Burning of garbage causing smoke", "Dead animal lying on the road"],
        "streetlights": ["Street light not working", "Entire lane is dark at night as the lights are off", "Street light pole is tilted and dangerous", "Light keeps flickering the whole night", "Street light is on during daytime, wasting power", "Wires hanging from street light pole"],
        "encroachment": ["Hawkers have blocked the entire footpath", "Illegal construction on public land", "Shop owners have extended their stalls onto the road", "Vehicles parked illegally block the lane", "Unauthorised temporary shed on the road", "Encroachment on the nala bank"],
        "public_health": ["Mosquito breeding due to stagnant water", "Fever cases rising, request fogging", "Stray dogs are attacking residents", "Public toilet is unhygienic", "Rats and flies problem near the market", "Contaminated water suspected, many people have stomach infection"],
        "electricity": ["Power cut for more than five hours", "Voltage fluctuation damaging appliances", "Transformer sparking near the house", "Electric wire fallen on the road", "Meter box open and dangerous", "Frequent power tripping since last week"],
        "parks": ["Park gate is locked during the day", "Broken swings and play equipment in the park", "Park not maintained, overgrown grass", "No lighting inside the garden", "Trees need trimming near the park", "Benches and walking track damaged"],
        "noise": ["Loud speakers playing late at night", "Construction work continuing after permitted hours", "Loud music from banquet hall disturbs residents", "Generator noise from commercial complex", "Factory machinery noise at night", "Firecrackers being burst late at night"],
    },
    "hi": {
        "water_supply": ["सुबह से पानी की सप्लाई बंद है", "नल में पानी का प्रेशर बहुत कम है", "नल से गंदा और बदबूदार पानी आ रहा है", "पानी का टैंकर समय पर नहीं आया"],
        "drainage": ["नाली का पानी सड़क पर बह रहा है", "गंदा पानी घरों में घुस रहा है", "मैनहोल का ढक्कन टूटा हुआ और खुला है", "बारिश के बाद सड़क पर पानी भर गया है"],
        "roads": ["सड़क के बीच में बड़ा गड्ढा हो गया है", "सड़क बुरी तरह टूटी है, दोपहिया वाहनों के लिए खतरनाक", "खुदाई के बाद सड़क धंस गई है", "फुटपाथ की टाइलें टूटी हुई हैं"],
        "garbage": ["कई दिनों से कचरा नहीं उठाया गया", "कचरे का डिब्बा भरकर बाहर बह रहा है", "खाली प्लॉट में खुले में कचरा फेंका जा रहा है", "आज कचरा गाड़ी नहीं आई"],
        "streetlights": ["स्ट्रीट लाइट काम नहीं कर रही है", "पूरी गली में रात को अंधेरा रहता है", "स्ट्रीट लाइट का खंभा झुका हुआ है", "स्ट्रीट लाइट रात भर टिमटिमाती रहती है"],
        "encroachment": ["फेरीवालों ने पूरा फुटपाथ घेर लिया है", "सार्वजनिक जमीन पर अवैध निर्माण हो रहा है", "दुकानदारों ने सड़क तक सामान फैला रखा है", "गलत तरीके से खड़ी गाड़ियां गली रोक रही हैं"],
        "public_health": ["रुके हुए पानी में मच्छर पनप रहे हैं", "बुखार के मामले बढ़ रहे हैं, फॉगिंग करवाएं", "आवारा कुत्ते लोगों पर हमला कर रहे हैं", "सार्वजनिक शौचालय बहुत गंदा है"],
        "electricity": ["पांच घंटे से ज्यादा बिजली गुल है", "वोल्टेज कम-ज्यादा होने से उपकरण खराब हो रहे हैं", "ट्रांसफॉर्मर से चिंगारी निकल रही है", "बिजली का तार सड़क पर गिरा हुआ है"],
        "parks": ["पार्क का गेट दिन में बंद रहता है", "पार्क में झूले टूटे हुए हैं", "पार्क की देखभाल नहीं हो रही, घास बहुत बढ़ गई है", "बगीचे में रोशनी नहीं है"],
        "noise": ["रात देर तक लाउडस्पीकर बज रहे हैं", "अनुमति के समय के बाद भी निर्माण कार्य चल रहा है", "बैंक्वेट हॉल की तेज आवाज़ से परेशानी हो रही है", "कमर्शियल कॉम्प्लेक्स के जनरेटर का शोर"],
    },
    "mr": {
        "water_supply": ["सकाळपासून पाणीपुरवठा बंद आहे", "नळाला पाण्याचा दाब खूप कमी आहे", "नळातून गढूळ आणि दुर्गंधीयुक्त पाणी येत आहे", "पाण्याचा टँकर वेळेवर आला नाही"],
        "drainage": ["गटारीचे पाणी रस्त्यावर वाहत आहे", "सांडपाणी घरात शिरत आहे", "मॅनहोलचे झाकण तुटलेले आणि उघडे आहे", "पावसानंतर रस्त्यावर पाणी साचले आहे"],
        "roads": ["रस्त्याच्या मधोमध मोठा खड्डा पडला आहे", "रस्ता खूप खराब झाला आहे, दुचाकीस्वारांसाठी धोकादायक", "खोदकामानंतर रस्ता खचला आहे", "पदपथाच्या फरशा तुटल्या आहेत"],
        "garbage": ["अनेक दिवसांपासून कचरा उचलला गेला नाही", "कचराकुंडी भरून बाहेर वाहत आहे", "मोकळ्या जागेत उघड्यावर कचरा टाकला जात आहे", "आज घंटागाडी आली नाही"],
        "streetlights": ["पथदिवा बंद आहे", "रात्री संपूर्ण गल्लीत अंधार असतो", "पथदिव्याचा खांब वाकलेला आहे", "पथदिवा रात्रभर लुकलुकत असतो"],
        "encroachment": ["फेरीवाल्यांनी संपूर्ण पदपथ अडवला आहे", "सार्वजनिक जागेवर अनधिकृत बांधकाम सुरू आहे", "दुकानदारांनी रस्त्यापर्यंत माल मांडला आहे", "चुकीच्या पद्धतीने उभी केलेली वाहने गल्ली अडवत आहेत"],
        "public_health": ["साचलेल्या पाण्यात डासांची पैदास होत आहे", "तापाचे रुग्ण वाढत आहेत, फवारणी करा", "भटकी कुत्री नागरिकांवर हल्ला करत आहेत", "सार्वजनिक शौचालय अतिशय अस्वच्छ आहे"],
        "electricity": ["पाच तासांहून अधिक वेळ वीज गेली आहे", "व्होल्टेज कमी-जास्त होऊन उपकरणे खराब होत आहेत", "ट्रान्सफॉर्मरमधून ठिणग्या येत आहेत", "विजेची तार रस्त्यावर पडली आहे"],
        "parks": ["उद्यानाचे गेट दिवसा बंद असते", "उद्यानातील झोपाळे तुटले आहेत", "उद्यानाची देखभाल होत नाही, गवत खूप वाढले आहे", "बागेत प्रकाश व्यवस्था नाही"],
        "noise": ["रात्री उशिरापर्यंत स्पीकर वाजत आहेत", "परवानगीच्या वेळेनंतरही बांधकाम सुरू आहे", "मंगल कार्यालयाच्या मोठ्या आवाजाचा त्रास होत आहे", "व्यावसायिक संकुलातील जनरेटरचा आवाज"],
    },
    "hinglish": {
        "water_supply": ["Subah se paani ki supply band hai", "Nal me paani ka pressure bahut kam hai", "Nal se ganda aur badbudar paani aa raha hai", "Paani ka tanker time par nahi aaya"],
        "drainage": ["Naali ka paani road par beh raha hai", "Ganda paani ghar me ghus raha hai", "Manhole ka dhakkan toota hua aur khula hai", "Baarish ke baad road par paani bhar gaya hai"],
        "roads": ["Road ke beech me bada gadda ho gaya hai", "Road bahut kharab hai, two-wheeler ke liye khatarnak", "Khudai ke baad road dhans gayi hai", "Footpath ki tiles toot gayi hain"],
        "garbage": ["Kai dinon se kachra nahi uthaya gaya", "Dustbin bhar kar bahar beh raha hai", "Khali plot me khule me kachra fenka ja raha hai", "Aaj kachra gaadi nahi aayi"],
        "streetlights": ["Street light kaam nahi kar rahi", "Poori gali me raat ko andhera rehta hai", "Street light ka pole jhuka hua hai", "Street light raat bhar flicker karti hai"],
        "encroachment": ["Hawkers ne poora footpath gher liya hai", "Public zameen par illegal construction ho raha hai", "Dukandaaron ne road tak saaman faila rakha hai", "Galat parking se gali block ho jaati hai"],
        "public_health": ["Ruke hue paani me machhar pan rahe hain", "Bukhar ke cases badh rahe hain, fogging karwaiye", "Awara kutte logon par hamla kar rahe hain", "Public toilet bahut gandi hai"],
        "electricity": ["Paanch ghante se zyada light gayi hui hai", "Voltage up-down hone se appliances kharab ho rahe hain", "Transformer se chingari nikal rahi hai", "Bijli ka taar road par gira hua hai"],
        "parks": ["Park ka gate din me band rehta hai", "Park ke jhoole toote hue hain", "Park ki dekhbhal nahi ho rahi, ghaas bahut badh gayi hai", "Garden me light nahi hai"],
        "noise": ["Raat der tak loudspeaker baj rahe hain", "Permission ke time ke baad bhi construction chal raha hai", "Banquet hall ki tez awaaz se pareshani hai", "Commercial complex ke generator ka shor"],
    },
}

# extra problem phrases used only by injected bursts: key -> lang -> phrases
BURST_PROBLEMS: dict[str, dict[str, list[str]]] = {
    "contam": {
        "en": ["Brown muddy water with a strong smell coming from the tap", "Many families in our lane have vomiting and diarrhoea after drinking tap water", "Water supply appears contaminated with sewage"],
        "hi": ["नल से भूरा गंदा पानी और तेज बदबू आ रही है", "नल का पानी पीने के बाद कई लोगों को उल्टी-दस्त हो गए हैं"],
        "mr": ["नळातून तपकिरी गढूळ पाणी आणि तीव्र दुर्गंधी येत आहे", "नळाचे पाणी प्यायल्यानंतर अनेकांना उलट्या-जुलाब झाले आहेत"],
        "hinglish": ["Nal se brown ganda paani aur tez badbu aa rahi hai", "Nal ka paani peene ke baad kai logon ko ulti-dast ho gaye hain"],
    },
    "strike": {
        "en": ["Garbage collection has completely stopped and waste is piling up", "Sanitation workers have not come for over a week, the whole street stinks", "Heaps of garbage on the road are attracting stray animals"],
        "hi": ["कचरा उठाना पूरी तरह बंद है, ढेर लग गया है", "सफाई कर्मचारी हफ्ते भर से नहीं आए, पूरी गली में बदबू है"],
        "mr": ["कचरा उचलणे पूर्णपणे बंद आहे, कचऱ्याचे ढीग साचले आहेत", "सफाई कर्मचारी आठवडाभर आलेले नाहीत, संपूर्ण गल्लीत दुर्गंधी आहे"],
        "hinglish": ["Kachra uthna poori tarah band hai, dher lag gaya hai", "Safai karmchari hafte bhar se nahi aaye, poori gali me badbu hai"],
    },
    "lights": {
        "en": ["All street lights in our lane went off suddenly and it is pitch dark", "Dark stretch has led to chain snatching incidents, restore lights", "Street lights on the main road have been off for days"],
        "hi": ["हमारी गली की सभी स्ट्रीट लाइटें अचानक बंद हो गईं, घना अंधेरा है", "अंधेरे के कारण चोरी की घटनाएं बढ़ी हैं, लाइटें चालू करें"],
        "mr": ["आमच्या गल्लीतील सर्व पथदिवे अचानक बंद झाले, गडद अंधार आहे", "अंधारामुळे चोरीच्या घटना वाढल्या आहेत, दिवे सुरू करा"],
        "hinglish": ["Hamari gali ki saari street lights achanak band ho gayi, ghana andhera hai", "Andhere ki wajah se chori ki ghatnayein badh gayi hain, lights chalu karein"],
    },
    "flood": {
        "en": ["Water has entered ground floor houses and shops, knee-deep flooding", "Heavy rain flooded the road and vehicles are stuck", "Drains are overflowing everywhere, sewage mixed with rainwater"],
        "hi": ["घरों और दुकानों में पानी घुस गया है, घुटनों तक पानी भरा है", "भारी बारिश से सड़क डूब गई, गाड़ियां फंसी हैं"],
        "mr": ["घरांमध्ये आणि दुकानांमध्ये पाणी शिरले आहे, गुडघ्यापर्यंत पाणी साचले आहे", "मुसळधार पावसाने रस्ता पाण्याखाली गेला, वाहने अडकली आहेत"],
        "hinglish": ["Ghar aur dukaanon me paani ghus gaya hai, ghutnon tak paani bhara hai", "Bhaari baarish se road doob gayi, gaadiyan fansi hain"],
    },
    "dengue": {
        "en": ["Several dengue cases reported in our building, request fogging immediately", "Mosquito breeding in stagnant water at the vacant plot and children are falling sick", "Fever and dengue cases rising in the locality, no fogging done"],
        "hi": ["हमारी बिल्डिंग में डेंगू के कई मामले आए हैं, तुरंत फॉगिंग कराएं", "खाली प्लॉट के जमा पानी में मच्छर पनप रहे हैं, बच्चे बीमार पड़ रहे हैं"],
        "mr": ["आमच्या इमारतीत डेंग्यूचे अनेक रुग्ण आढळले आहेत, तात्काळ फवारणी करा", "मोकळ्या प्लॉटमधील साचलेल्या पाण्यात डास वाढले आहेत, मुले आजारी पडत आहेत"],
        "hinglish": ["Hamari building me dengue ke kai cases aaye hain, turant fogging karwaiye", "Khali plot ke jame paani me machhar pan rahe hain, bachche bimar pad rahe hain"],
    },
    "potholes": {
        "en": ["Ring road has multiple deep potholes causing accidents", "Two-wheeler riders are falling due to craters on the ring road", "Road resurfacing was promised but potholes have only grown"],
        "hi": ["रिंग रोड पर कई गहरे गड्ढे हैं, हादसे हो रहे हैं", "रिंग रोड के गड्ढों में गिरकर दोपहिया सवार घायल हो रहे हैं"],
        "mr": ["रिंग रोडवर अनेक खोल खड्डे आहेत, अपघात होत आहेत", "रिंग रोडवरील खड्ड्यांत पडून दुचाकीस्वार जखमी होत आहेत"],
        "hinglish": ["Ring road par kai gehre gadde hain, accident ho rahe hain", "Ring road ke gaddon me girkar two-wheeler sawaar ghayal ho rahe hain"],
    },
    "encroach": {
        "en": ["New illegal stalls have come up around the market entrance blocking traffic", "Encroachment near the market gate has blocked the ambulance route", "Unauthorised vendors occupied the entire service road"],
        "hi": ["बाजार के गेट के पास नए अवैध ठेले लग गए हैं, ट्रैफिक जाम रहता है", "बाजार के प्रवेश द्वार पर अतिक्रमण से एम्बुलेंस का रास्ता बंद है"],
        "mr": ["बाजाराच्या प्रवेशद्वाराजवळ नवीन अनधिकृत टपऱ्या उभ्या राहिल्या आहेत, वाहतूक कोंडी होते", "बाजाराच्या गेटजवळील अतिक्रमणामुळे रुग्णवाहिकेचा मार्ग बंद आहे"],
        "hinglish": ["Market gate ke paas naye illegal thele lag gaye hain, traffic jam rehta hai", "Market ke entrance par encroachment se ambulance ka raasta band hai"],
    },
}

LOC_TEMPLATES = {
    "en": ["near {lm}", "on {st}", "in {area} area", "opposite {lm}", "at {st}, close to {lm}", "behind {lm}", "in {area}, near {lm}"],
    "hi": ["{lm} के पास", "{st} पर", "{area} इलाके में", "{lm} के सामने", "{st}, {lm} के नज़दीक"],
    "mr": ["{lm} जवळ", "{st} वर", "{area} परिसरात", "{lm} समोर", "{st}, {lm} जवळ"],
    "hinglish": ["{lm} ke paas", "{st} par", "{area} area me", "{lm} ke saamne", "{st}, {lm} ke nazdeek"],
}
DURATION = {
    "en": ["for the past {n} days", "since {n} days", "since yesterday", "since this morning", "", "", ""],
    "hi": ["पिछले {n} दिनों से", "कल से", "आज सुबह से", "", ""],
    "mr": ["गेल्या {n} दिवसांपासून", "कालपासून", "आज सकाळपासून", "", ""],
    "hinglish": ["pichhle {n} dino se", "kal se", "aaj subah se", "", ""],
}
URGENCY = {
    "en": ["Please take immediate action.", "Kindly resolve at the earliest.", "This is a health hazard for children and senior citizens.", "I have complained earlier but nobody responded.", "Request urgent attention.", "", "", ""],
    "hi": ["कृपया तुरंत कार्रवाई करें।", "जल्द से जल्द समाधान करें।", "बच्चों और बुजुर्गों के लिए खतरा है।", "पहले भी शिकायत की थी, कोई जवाब नहीं मिला।", "", ""],
    "mr": ["कृपया तात्काळ कार्यवाही करावी.", "लवकरात लवकर निराकरण करावे.", "लहान मुले आणि वृद्धांसाठी धोकादायक आहे.", "यापूर्वीही तक्रार केली होती, प्रतिसाद मिळाला नाही.", "", ""],
    "hinglish": ["Kripya turant action lein.", "Jaldi se jaldi solve karein.", "Bachchon aur buzurgon ke liye khatra hai.", "Pehle bhi complaint ki thi, koi response nahi mila.", "", ""],
}

FEEDBACK_TEXT = {
    "low": {
        "en": ["Complaint was not resolved even after several reminders", "Very slow response from the department", "Officials did not visit, only the status was marked closed"],
        "hi": ["कई बार याद दिलाने के बाद भी शिकायत का समाधान नहीं हुआ", "विभाग का जवाब बहुत धीमा है"],
        "mr": ["अनेकदा आठवण करून देऊनही तक्रारीचे निराकरण झाले नाही", "विभागाचा प्रतिसाद खूप संथ आहे"],
        "hinglish": ["Kai baar yaad dilane ke baad bhi complaint solve nahi hui", "Department ka response bahut slow hai"],
    },
    "mid": {
        "en": ["Issue was fixed but it took longer than promised", "Work done partially, need a follow-up"],
        "hi": ["समस्या ठीक हुई लेकिन वादे से ज्यादा समय लगा"],
        "mr": ["समस्या सुटली पण अपेक्षेपेक्षा जास्त वेळ लागला"],
        "hinglish": ["Problem solve hui lekin promise se zyada time laga"],
    },
    "high": {
        "en": ["Problem resolved quickly, thank you", "Prompt action by the ward staff, very satisfied", "Good service, the team was courteous"],
        "hi": ["समस्या जल्दी सुलझ गई, धन्यवाद", "वार्ड कर्मचारियों की त्वरित कार्रवाई, बहुत संतुष्ट हूं"],
        "mr": ["समस्या लवकर सुटली, धन्यवाद", "वॉर्ड कर्मचाऱ्यांची तत्पर कारवाई, खूप समाधानी आहे"],
        "hinglish": ["Problem jaldi solve ho gayi, thank you", "Ward staff ki turant action, bahut khush hoon"],
    },
}

FIRST_NAMES = ["Rajesh", "Sunita", "Amit", "Priya", "Suresh", "Anita", "Vijay", "Kavita", "Ramesh", "Meena", "Sanjay", "Pooja", "Anil", "Sneha", "Manoj", "Rekha", "Prakash", "Jyoti", "Deepak", "Shilpa", "Ganesh", "Savita", "Nitin", "Asha", "Mahesh", "Vandana", "Rahul", "Sunil", "Swati", "Yogesh"]
LAST_NAMES = ["Patil", "Deshmukh", "Kulkarni", "Jadhav", "Shinde", "Pawar", "More", "Gaikwad", "Sharma", "Verma", "Gupta", "Singh", "Yadav", "Joshi", "Kadam", "Chavan", "Bhosale", "Mane", "Thakur", "Khan", "Shaikh", "Nair", "Iyer", "Mishra"]
