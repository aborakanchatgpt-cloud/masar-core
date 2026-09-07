# مصادر بلا API آلي رسمي — لا تُكشط، تُسجَّل هنا فقط

هذا الملف يوثّق الشركات الكبرى التي **يُفترض أنها توظّف بكثافة في السعودية/الخليج**
لكن لا تملك واجهة برمجية عامة بدون مصادقة (JSON API) يمكن قراءتها آليًا وفق قيود
مسار (§4.5 من دليل التنفيذ): ATS مغلق (Oracle Taleo، SAP SuccessFactors، Workday
بدون endpoint عام موثّق، iCIMS خاص) أو صفحة وظائف بلا JSON-LD ولا RSS ولا API.

**لا يُبنى أي جامع لهذه الشركات ولا يُكشط موقعها.** إن ظهر لاحقًا أن إحداها تطلق
واجهة عامة (أو RSS رسمي، أو تنتقل لمزوّد ATS مدعوم)، تُنقل من هنا إلى
`data/sources_seed.csv` بعد تحقق فعلي (Source Curator الأسبوعي، أو تنفيذ لاحق).

| الشركة | النظام المُلاحَظ / السبب | ملاحظة |
|---|---|---|
| Saudi Aramco | Oracle/نظام توظيف داخلي مغلق، لا API عام | أكبر مشغّل هندسي بالمملكة؛ لا مصدر آلي |
| SABIC | SAP SuccessFactors على الأرجح، لا API عام موثّق | يُعاد الفحص دوريًا (Source Curator) |
| SATORP | نظام مغلق تابع لأرامكو/توتال | لا مصدر آلي رسمي |
| YASREF | نظام مغلق تابع لأرامكو | لا مصدر آلي رسمي |
| Sasref | نظام مغلق | لا مصدر آلي رسمي |
| Ma'aden (معادن) | نظام توظيف مؤسسي مغلق (يبدو SuccessFactors) | لا مصدر آلي رسمي |
| SABIC Agri-Nutrients (سبكيم/التصنيع) | أنظمة مؤسسية مغلقة متفرقة | يحتاج فحصًا فرديًا لكل شركة تصنيع |
| Petro Rabigh | نظام مغلق | لا مصدر آلي رسمي |
| Marafiq | نظام مغلق | لا مصدر آلي رسمي |
| ACWA Power | صفحة careers.acwapower.com بلا API/RSS/JSON-LD واضح وقت الفحص | يُعاد فحصه لاحقًا (قد يضيف JSON-LD) |
| NEOM | careers.neom.com — منصّة توظيف مخصّصة، لا API عام موثّق وقت الفحص | يُعاد فحصه (مشروع كبير النمو، يستحق مراجعة دورية) |
| Red Sea Global | صفحة careers مخصّصة بلا API عام | لا مصدر آلي رسمي حاليًا |
| الشركة السعودية للكهرباء (SEC) | نظام توظيف حكومي/شبه حكومي مغلق | لا مصدر آلي رسمي |
| المياه الوطنية (NWC) | نظام توظيف حكومي مغلق | لا مصدر آلي رسمي |
| Jotun / Hempel / National Paints / Sigma Paints | مواقع شركات دهانات عالمية — صفحات وظائف بلا JSON-LD ثابت وقت الفحص | تُفحص فرديًا لاحقًا؛ بعضها قد يستضيف صفحات على Workday |
| United Float Glass / Obeikan | مواقع مؤسسية دون بوابة توظيف آلية واضحة | لا مصدر آلي رسمي |
| Al Muneef / Future Pipe / Amiantit / Saudi Pipes / Perma-Pipe | صناعة الأنابيب — لا API/RSS ظاهر لأي منها وقت الفحص | تحتاج فحصًا فرديًا لاحقًا |
| Yamama Cement / Saudi Cement / Southern Cement | نظام توظيف مؤسسي داخلي | لا مصدر آلي رسمي |
| Almarai / Savola / PepsiCo Saudi / Coca-Cola Saudi | أغذية ومشروبات كبرى — أنظمة SAP SuccessFactors على الأرجح | لا API عام موثّق وقت الفحص |
| SPIMACO / Jamjoom Pharma | نظام توظيف مؤسسي مغلق | لا مصدر آلي رسمي |
| Worley / KBR / Jacobs / Fluor / Bechtel / Hill International / Parsons / AECOM / Larsen & Toubro | شركات EPC/استشارات هندسية عملاقة — تستخدم Workday أو Oracle Taleo أو iCIMS بدون endpoint عام غير موثّق (بعضها له صفحة Workday كـ`*.wd*.myworkdayjobs.com` لكن تحتاج تحديد tenant/site دقيق لكل شركة على حدة قبل اعتمادها كمصدر — لم يُتحقق أي منها فعليًا خلال هذه الجلسة) | مرشّحة لجولة فحص Workday مخصّصة لاحقًا؛ لم تُدرج بدون تحقق فعلي |
| Accor (فنادق أخرى غير SmartRecruiters) / IHG / Marriott / Hilton | أنظمة توظيف عالمية متفرقة (Taleo/Workday/داخلي)؛ IHG وHilton بلا API عام ظاهر | Accor نفسها أُضيفت عبر SmartRecruiters (انظر sources_seed.csv)؛ الباقي بلا مصدر آلي |
| طيبة لتشغيل المطارات / Nesma / Almabani / Alfanar / Al Yamama / Initial Saudi | تشغيل وصيانة — مواقع وظائف مؤسسية بلا API/RSS ظاهر | لا مصدر آلي رسمي |
| Elbait / QS Quest / Hire Fellows / JVI | وكالات توظيف مذكورة بالدليل — لم يُعثر لها على واجهة ATS عامة (Greenhouse/Lever/Workable/Recruitee) وقت الفحص | يُعاد البحث عنها لاحقًا (قد تستخدم نظامًا مغلقًا أو غير مفهرس بعد) |

## قاعدة العمل
لا يُضاف أي من هؤلاء إلى `sources_seed.csv` إلا بعد إيجاد:
- واجهة JSON عامة موثّقة (ATS معروف)، أو
- RSS/Atom رسمي، أو
- صفحة وظائف واحدة على الأقل تحمل `JobPosting` JSON-LD صريح (سيتيمة sitemap_jsonld)،

وبعد التحقق الفعلي (fetch حقيقي يعيد ≥ 1 وظيفة) — وليس افتراضًا.
