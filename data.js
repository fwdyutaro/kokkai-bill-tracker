/*
 * 法律案トラッカー サンプルデータ
 * 第221回国会（特別会, 令和8年2月18日〜7月17日）の実データに基づく。
 * 取得元: 参議院 議案情報 / 衆議院 審査経過 / 内閣法制局。
 * 「概要(AI)」「参考情報の確信度」はプロトタイプ用の付与例。
 * 一次資料リンクは検証済みURL、自動抽出候補は検索URLで表現している。
 */
window.BILLS = [
  {
    id: "221-kaku-31",
    diet: 221,
    dietLabel: "第221回（特別会）",
    no: "閣法 第31号",
    type: "閣法", // 閣法=内閣提出 / 衆法・参法=議員提出
    title: "重要施設の周辺地域の上空における小型無人機等の飛行の禁止に関する法律の一部を改正する法律案",
    shortTitle: "小型無人機(ドローン)飛行禁止法 改正案",
    ministry: "警察庁",
    submittedOn: "2026-03-24",
    status: "審議中",         // 審議中 / 成立 / 継続審査 / 廃案
    statusDetail: "参議院・本会議採決待ち",
    confidence: 78,           // 成立確度（機械推定の例）
    summary: "ドローン等の性能向上を踏まえ、飛行禁止の対象施設を追加し、規制対象となる周辺地域(レッドゾーン/イエローゾーン)を拡大。周辺地域での飛行に対し、警察官の命令違反を待たずに直接処罰できる規定(6月以下の拘禁刑または50万円以下の罰金)を新設する。",
    summaryNote: "提案理由・報道をもとにAIが要約（一次資料で要確認）",
    tags: ["安全保障", "ドローン", "重要インフラ", "罰則強化"],
    timeline: [
      { date: "2026-03-24", house: "提出", event: "内閣が法律案を提出（閣議決定）", result: "" },
      { date: "2026-05-19", house: "衆", event: "内閣委員会に付託", result: "" },
      { date: "2026-05-22", house: "衆", event: "内閣委員会 採決", result: "可決" },
      { date: "2026-05-26", house: "衆", event: "本会議 採決（起立・多数）", result: "可決" },
      { date: "2026-06-10", house: "参", event: "内閣委員会に付託", result: "" },
      { date: "2026-06-16", house: "参", event: "内閣委員会 採決", result: "可決" },
      { date: "2026-06-17", house: "参", event: "本会議 採決待ち", result: "予定" }
    ],
    refs: [
      { tier: 1, cat: "一次資料", pub: "参議院", title: "議案情報（審議経過）", url: "https://www.sangiin.go.jp/japanese/joho1/kousei/gian/221/meisai/m221080221031.htm" },
      { tier: 1, cat: "一次資料", pub: "内閣法制局", title: "第221回国会 内閣提出法律案（提案理由・要綱・新旧対照）", url: "https://www.clb.go.jp/recent-laws/diet_bill/id=5144" },
      { tier: 1, cat: "会議録", pub: "国立国会図書館", title: "会議録検索（衆参 内閣委員会の質疑）", url: "https://kokkai.ndl.go.jp/" },
      { tier: 1, cat: "審議会・検討会", pub: "内閣官房 小型無人機等対策推進室", title: "関連：小型無人機等の規制に関する検討資料", url: "https://www.kantei.go.jp/jp/singi/kogatamujinki/", confNote: "所管・時期からの自動紐付け候補" },
      { tier: 2, cat: "立法補佐機関", pub: "国立国会図書館 調査及び立法考査局", title: "調査と情報／レファレンス 等（ドローン規制）", url: "https://www.ndl.go.jp/jp/diet/publication/issue/index.html", conf: 71 },
      { tier: 4, cat: "解説", pub: "石田まさひろ政策研究会", title: "【法案解説シリーズ】小型無人機飛行禁止法 改正案", url: "https://www.masahiro-ishida.com/post-21332", conf: 88 },
      { tier: 4, cat: "法律事務所", pub: "（自動検索候補）", title: "ドローン規制改正に関するニュースレター", url: "https://www.google.com/search?q=ドローン+小型無人機+飛行禁止法+改正+2026+ニュースレター+法律事務所", conf: 64 }
    ]
  },
  {
    id: "221-kaku-33",
    diet: 221,
    dietLabel: "第221回（特別会）",
    no: "閣法 第33号",
    type: "閣法",
    title: "携帯音声通信事業者による契約者等の本人確認等及び携帯音声通信役務の不正な利用の防止に関する法律の一部を改正する法律案",
    shortTitle: "携帯電話不正利用防止法（本人確認）改正案",
    ministry: "総務省",
    submittedOn: "2026-03-24",
    status: "成立",
    statusDetail: "令和8年5月29日 公布（法律第25号）",
    confidence: 100,
    summary: "携帯端末向け電気通信役務の不正利用が多様化・巧妙化していることを踏まえ、本人確認の対象役務に音声通信以外の役務を追加。あわせて同一契約者が同時に利用できる端末数の制限措置を導入し、特殊詐欺等への悪用を抑止する。",
    summaryNote: "趣旨説明をもとにAIが要約（一次資料で要確認）",
    tags: ["通信", "本人確認", "特殊詐欺対策", "消費者保護"],
    timeline: [
      { date: "2026-03-24", house: "提出", event: "内閣が法律案を提出（閣議決定）", result: "" },
      { date: "2026-04-27", house: "衆", event: "総務委員会に付託", result: "" },
      { date: "2026-05-12", house: "衆", event: "総務委員会 採決", result: "可決" },
      { date: "2026-05-14", house: "衆", event: "本会議 採決", result: "可決" },
      { date: "2026-05-18", house: "参", event: "総務委員会に付託", result: "" },
      { date: "2026-05-21", house: "参", event: "総務委員会 採決", result: "可決" },
      { date: "2026-05-22", house: "参", event: "本会議 採決", result: "可決" },
      { date: "2026-05-29", house: "公布", event: "公布（法律第25号）", result: "成立" }
    ],
    refs: [
      { tier: 1, cat: "一次資料", pub: "参議院", title: "議案情報（審議経過）", url: "https://www.sangiin.go.jp/japanese/joho1/kousei/gian/221/meisai/m221080221033.htm" },
      { tier: 1, cat: "一次資料", pub: "内閣法制局", title: "第221回国会 内閣提出法律案（提案理由・要綱・新旧対照）", url: "https://www.clb.go.jp/recent-laws/diet_bill/id=5144" },
      { tier: 1, cat: "所管省庁", pub: "総務省", title: "国会提出法案（第221回国会・総務省）", url: "https://www.soumu.go.jp/menu_news/s-news/01kanbo02_02000081.html" },
      { tier: 1, cat: "審議会・検討会", pub: "総務省", title: "関連：電気通信事業ガバナンス／本人確認手法の検討会", url: "https://www.google.com/search?q=総務省+本人確認+検討会+携帯+不正利用", confNote: "所管・テーマからの自動紐付け候補" },
      { tier: 1, cat: "パブリックコメント", pub: "e-Gov", title: "施行規則改正等の意見募集（関連）", url: "https://public-comment.e-gov.go.jp/", confNote: "案件名・所管からの候補" },
      { tier: 2, cat: "立法補佐機関", pub: "参議院 常任委員会調査室", title: "「立法と調査」（総務委員会・本法案の論点）", url: "https://www.sangiin.go.jp/japanese/annai/chousa/rippou_chousa/backnumber.html", conf: 74 },
      { tier: 4, cat: "法律事務所", pub: "（自動検索候補）", title: "携帯電話本人確認義務の強化に関する解説", url: "https://www.google.com/search?q=携帯電話不正利用防止法+改正+本人確認+2026+解説+法律事務所", conf: 69 }
    ]
  },
  {
    id: "221-kaku-32",
    diet: 221,
    dietLabel: "第221回（特別会）",
    no: "閣法 第32号",
    type: "閣法",
    title: "株式会社海外通信・放送・郵便事業支援機構法の一部を改正する法律案",
    shortTitle: "海外通信・放送・郵便事業支援機構(JICT)法 改正案",
    ministry: "総務省",
    submittedOn: "2026-03-24",
    status: "成立",
    statusDetail: "令和8年5月7日 公布（法律第18号）",
    confidence: 100,
    summary: "日本企業の海外での通信・放送・郵便事業展開を支援する官民ファンド(JICT)について、保有する株式等・債権の譲渡その他の処分等の期限を10年間延長し、令和28年(2046年)3月31日までとする。",
    summaryNote: "趣旨説明をもとにAIが要約（一次資料で要確認）",
    tags: ["官民ファンド", "国際展開", "郵政・通信", "総務省所管法人"],
    timeline: [
      { date: "2026-03-24", house: "提出", event: "内閣が法律案を提出（閣議決定）", result: "" },
      { date: "2026-04-08", house: "衆", event: "総務委員会に付託", result: "" },
      { date: "2026-04-14", house: "衆", event: "総務委員会 採決", result: "可決" },
      { date: "2026-04-16", house: "衆", event: "本会議 採決", result: "可決" },
      { date: "2026-04-20", house: "参", event: "総務委員会に付託", result: "" },
      { date: "2026-04-23", house: "参", event: "総務委員会 採決", result: "可決" },
      { date: "2026-04-24", house: "参", event: "本会議 採決", result: "可決" },
      { date: "2026-05-07", house: "公布", event: "公布（法律第18号）", result: "成立" }
    ],
    refs: [
      { tier: 1, cat: "一次資料", pub: "参議院", title: "議案情報（審議経過）", url: "https://www.sangiin.go.jp/japanese/joho1/kousei/gian/221/meisai/m221080221032.htm" },
      { tier: 1, cat: "一次資料", pub: "内閣法制局", title: "第221回国会 内閣提出法律案（提案理由・要綱・新旧対照）", url: "https://www.clb.go.jp/recent-laws/diet_bill/id=5144" },
      { tier: 1, cat: "所管省庁", pub: "総務省", title: "国会提出法案（第221回国会・総務省）", url: "https://www.soumu.go.jp/menu_news/s-news/01kanbo02_02000081.html" },
      { tier: 1, cat: "会議録", pub: "国立国会図書館", title: "会議録検索（衆参 総務委員会）", url: "https://kokkai.ndl.go.jp/" },
      { tier: 2, cat: "公的研究／検査", pub: "会計検査院 等", title: "関連：官民ファンドJICTの運用状況に関する指摘", url: "https://www.google.com/search?q=JICT+海外通信放送郵便事業支援機構+会計検査院+官民ファンド", conf: 66, confNote: "テーマからの自動紐付け候補" },
      { tier: 3, cat: "シンクタンク", pub: "（自動検索候補）", title: "官民ファンドの政策評価レポート", url: "https://www.google.com/search?q=官民ファンド+JICT+評価+レポート+シンクタンク", conf: 58 }
    ]
  },
  {
    id: "217-shu-58",
    diet: 217,
    dietLabel: "第217回（提出）→継続",
    no: "衆法 第58号",
    type: "衆法",
    title: "郵政民営化法等の一部を改正する法律案",
    shortTitle: "郵政民営化法 改正案（議員立法）",
    ministry: "—（議員提出：山口俊一君外6名）",
    submittedOn: "2025-06-17",
    status: "継続審査",
    statusDetail: "衆議院で継続審査中（参議院未送付）",
    confidence: 25,
    summary: "議員提出（衆法）による郵政民営化法等の改正案。第217回国会で提出された後、会期内に審査を終えず継続審査となっている。※本文は第221回国会の議案ではなく、ステータスの多様性（継続審査の例）として収録。",
    summaryNote: "議案情報をもとにAIが要約（一次資料で要確認）",
    tags: ["郵政", "議員立法", "継続審査"],
    timeline: [
      { date: "2025-06-17", house: "提出", event: "衆議院議員が法律案を発議（山口俊一君外6名）", result: "" },
      { date: "2025-08-01", house: "衆", event: "総務委員会に付託", result: "" },
      { date: "2025-08-05", house: "衆", event: "総務委員会「継続審査」議決", result: "継続" },
      { date: "2025-08-05", house: "衆", event: "本会議「継続審査」決定（起立・多数）", result: "継続" }
    ],
    refs: [
      { tier: 1, cat: "一次資料", pub: "参議院", title: "議案情報（審議経過）", url: "https://www.sangiin.go.jp/japanese/joho1/kousei/gian/218/meisai/m218090217058.htm" },
      { tier: 1, cat: "会議録", pub: "国立国会図書館", title: "会議録検索（総務委員会）", url: "https://kokkai.ndl.go.jp/" },
      { tier: 2, cat: "立法補佐機関", pub: "衆議院 調査局／国立国会図書館", title: "郵政民営化をめぐる論点の調査資料", url: "https://www.ndl.go.jp/jp/diet/publication/issue/index.html", conf: 60 },
      { tier: 3, cat: "シンクタンク", pub: "（自動検索候補）", title: "郵政民営化・ユニバーサルサービスに関する分析", url: "https://www.google.com/search?q=郵政民営化法+改正+論点+レポート", conf: 52 }
    ]
  }
];
