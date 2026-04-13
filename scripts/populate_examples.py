"""One-off script to populate bias examples from fetched article data."""

from __future__ import annotations

import json
from pathlib import Path

BIASES_PATH = Path(__file__).resolve().parent.parent / "docs" / "_data" / "source_biases.json"

# Examples gathered from real articles via web fetch
EXAMPLES = {
    "aljazeera": {
        "Pro-Arab, anti-Israel framing": {
            "example_text": "the US-Israel war on Iran stretches into its fifth week",
            "example_url": "https://www.aljazeera.com/news/2026/3/30/trump-wants-to-invade-iran-to-seize-oil-calls-us-objectors-stupid",
            "unbiased_text": "The conflict between the US, Israel, and Iran enters its fifth week",
        },
        "Speculation and amplification": {
            "example_text": "discussions within the administration over the past month have touched upon the possible seizure of Kharg Island",
            "example_url": "https://www.aljazeera.com/news/2026/3/30/trump-wants-to-invade-iran-to-seize-oil-calls-us-objectors-stupid",
            "unbiased_text": "Unconfirmed reports suggest some administration officials have discussed the possibility of seizing Kharg Island, though no formal policy decision has been made",
        },
        "Conflict expansion and contagion framing": {
            "example_text": "escalation on multiple fronts of the US-Israel war on Iran",
            "example_url": "https://www.aljazeera.com/economy/2026/3/30/oil-rises-above-116-a-barrel-as-iran-accuses-us-of-preparing-invasion",
            "unbiased_text": "Military activity involving multiple parties in the region has intensified",
        },
        "Casualty and victimization emphasis": {
            "example_text": "We are not talking about stone and mortar. We are talking about the memory and history of a people.",
            "example_url": "https://www.aljazeera.com/features/2026/4/1/irans-minister-civilisational-identity-under-fire-in-unprecedented-war",
            "unbiased_text": "Iran's cultural minister stated that the damage to heritage sites represents a loss of historical and cultural significance, with at least 56 museums and monuments reportedly affected.",
        },
        "Conspiracy distraction meta-narrative": {
            "example_text": "No one in the market has ever seen the outages we are now suffering from",
            "example_url": "https://www.aljazeera.com/economy/2026/3/30/oil-rises-above-116-a-barrel-as-iran-accuses-us-of-preparing-invasion",
            "unbiased_text": "Market participants described the current supply disruptions as among the most significant in recent years",
        },
        "Anti-war voice platforming": {
            "example_text": "The only way to avoid grave economic consequences is to end the war as soon as possible.",
            "example_url": "https://www.aljazeera.com/opinions/2026/3/23/why-the-oil-and-gas-price-shock-from-the-iran-war-wont-just-fade-away",
            "unbiased_text": "Analysts note that an end to hostilities would reduce economic disruption, though other policy options such as strategic reserve releases and production increases could also mitigate effects.",
        },
        "Economic crisis escalation framing": {
            "example_text": "plunging the world into its biggest energy crisis in decades",
            "example_url": "https://www.aljazeera.com/economy/2026/3/30/oil-rises-above-116-a-barrel-as-iran-accuses-us-of-preparing-invasion",
            "unbiased_text": "Oil prices rose significantly, with analysts comparing the disruption to previous energy supply shocks",
        },
        "Historical trade disruption magnitude comparison": {
            "example_text": "close to its highest point of $147 recorded in July 2008",
            "example_url": "https://www.aljazeera.com/opinions/2026/3/23/why-the-oil-and-gas-price-shock-from-the-iran-war-wont-just-fade-away",
            "unbiased_text": "Oil prices have risen toward levels last seen during the 2008 price spike, though market conditions and global supply dynamics differ significantly from that period.",
        },
        "Trump rhetoric and policy framing": {
            "example_text": "Trump also repeated claims that Iran's new Supreme Leader Mojtaba Khamenei was injured in the war.",
            "example_url": "https://www.aljazeera.com/news/2026/3/30/trump-wants-to-invade-iran-to-seize-oil-calls-us-objectors-stupid",
            "unbiased_text": "Trump repeated unverified claims that Iran's Supreme Leader was injured. These claims have not been independently confirmed.",
        },
        "Non-conflict topic politicization": {
            "example_text": "For the GCC states, this will not be merely a market shock but an existential challenge to their role as reliable suppliers.",
            "example_url": "https://www.aljazeera.com/opinions/2026/3/23/why-the-oil-and-gas-price-shock-from-the-iran-war-wont-just-fade-away",
            "unbiased_text": "Gulf Cooperation Council states face pressure on their oil supply reliability as the conflict disrupts regional shipping routes.",
        },
        "European policy and institutional focus": {
            "example_text": "Persistent high prices will force consumers and industries to curb their consumption.",
            "example_url": "https://www.aljazeera.com/opinions/2026/3/23/why-the-oil-and-gas-price-shock-from-the-iran-war-wont-just-fade-away",
            "unbiased_text": "Sustained higher energy prices may lead to reduced consumption in some sectors, though the extent of demand destruction depends on price duration and alternative supply options.",
        },
    },
    "euronews": {
        "EU-centric news priority": {
            "example_text": "A crisis is looming on European farms as the war on Iran threatens fertiliser supplies",
            "example_url": "http://www.euronews.com/2026/03/28/europes-farms-are-reeling-from-the-iran-war-regenerative-farmers-saw-it-coming",
            "unbiased_text": "The conflict in Iran is disrupting global fertiliser supply chains, with European farmers among those affected by potential shortages",
        },
        "Diplomatic process framing": {
            "example_text": "Objectives of the war appear to constantly be shifting, but how successful has Washington been so far?",
            "example_url": "http://www.euronews.com/2026/03/28/how-successful-has-the-us-been-in-achieving-its-war-objectives-in-its-now-one-month-old-wa",
            "unbiased_text": "Military analysts assess the extent to which the US has achieved its stated objectives one month into the conflict",
        },
        "European policy and institutional focus": {
            "example_text": "The EU's Common Agricultural Policy (CAP) does reward farmers for environmental, climate and...friendly actions",
            "example_url": "http://www.euronews.com/2026/03/28/europes-farms-are-reeling-from-the-iran-war-regenerative-farmers-saw-it-coming",
            "unbiased_text": "Some agricultural policies, including the EU's CAP, provide incentives for environmentally sustainable farming practices that may offer resilience during supply disruptions",
        },
        "Speculation and amplification": {
            "example_text": "disrupted global supply chains and caused an international oil price crisis, as attacks on energy infrastructure continue",
            "example_url": "http://www.euronews.com/2026/03/28/how-successful-has-the-us-been-in-achieving-its-war-objectives-in-its-now-one-month-old-wa",
            "unbiased_text": "The conflict has disrupted supply chains and contributed to higher oil prices as military operations affecting energy infrastructure continue",
        },
        "Economic crisis escalation framing": {
            "example_text": "Synthetic fertiliser can account for up to 12 per cent of total input costs. This rises sharply during price spikes.",
            "example_url": "http://www.euronews.com/2026/03/28/europes-farms-are-reeling-from-the-iran-war-regenerative-farmers-saw-it-coming",
            "unbiased_text": "Synthetic fertiliser typically represents about 12% of farm input costs, a proportion that increases when global commodity prices rise",
        },
        "Conflict expansion and contagion framing": {
            "example_text": "protest against US President Donald Trump on a range of different issues, in what they see as his authoritatian style of governance, hardline immigration policies, climate change denial and the war with Iran",
            "example_url": "http://www.euronews.com/2026/03/28/huge-crowds-protest-against-trump-on-no-kings-day-in-the-us-and-abroad",
            "unbiased_text": "Protesters demonstrated against various Trump administration policies, including immigration enforcement, environmental policy, and the military campaign in Iran.",
        },
        "Casualty and victimization emphasis": {
            "example_text": "its undertaking would 'probably' result in US service member casualties",
            "example_url": "https://www.euronews.com/2026/04/01/securing-irans-highly-enriched-uranium-stockpiles-could-prove-risky-experts-say",
            "unbiased_text": "Military experts assess that securing the stockpiles would involve operational risks including potential casualties.",
        },
        "Nuclear threat framing": {
            "example_text": "Iran's stockpile could allow it to build as many as 10 nuclear bombs should the country decide to weaponise its programme.",
            "example_url": "https://www.euronews.com/2026/04/01/securing-irans-highly-enriched-uranium-stockpiles-could-prove-risky-experts-say",
            "unbiased_text": "Experts estimate Iran's enriched uranium stockpile could theoretically be sufficient for multiple nuclear devices, though Iran maintains its program is peaceful.",
        },
    },
    "france24": {
        "European diplomatic lens": {
            "example_text": "Europe pushes back on US military operations as concerns over Iran war mount",
            "example_url": "https://www.france24.com/en/europe-pushes-back-on-us-military-operations-as-concerns-over-iran-war-mount",
            "unbiased_text": "Several European governments expressed reservations about the scope of US military operations in Iran",
        },
        "Selective distancing from Trump rhetoric": {
            "example_text": "'Blatantly racist': Death penalty law among 'worst legislation' in Israeli parliamentary history",
            "example_url": "https://www.france24.com/en/blatantly-racist-death-penalty-law-among-worst-legislation-in-israeli-parliamentary-history",
            "unbiased_text": "Critics describe Israel's new death penalty legislation as discriminatory, while supporters argue it addresses security concerns",
        },
    },
    "reuters": {
        "Wire service neutrality": {
            "example_text": "Oil whiplash: Iran war shock to flip market to deficit in 2026, analysts say",
            "example_url": "https://www.investing.com/news/commodities-news/oil-whiplash-iran-war-shock-to-flip-market-to-deficit-in-2026-analysts-say-4608356",
            "unbiased_text": "Analysts at major banks project the Iran conflict will shift oil markets into a supply deficit for 2026",
        },
    },
}


def main():
    data = json.loads(BIASES_PATH.read_text())

    populated = 0
    for source_key, examples in EXAMPLES.items():
        if source_key not in data:
            continue
        for bias in data[source_key]["biases"]:
            pattern = bias["pattern"]
            if pattern in examples and not bias.get("example_text"):
                ex = examples[pattern]
                bias["example_text"] = ex["example_text"]
                bias["example_url"] = ex["example_url"]
                bias["unbiased_text"] = ex["unbiased_text"]
                populated += 1

    BIASES_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"Populated {populated} bias examples")

    # Report which biases still need examples
    total = 0
    missing = 0
    for source_key, info in data.items():
        for bias in info["biases"]:
            total += 1
            if not bias.get("example_text"):
                missing += 1
                print(f"  Missing: {info['display_name']} / {bias['pattern']}")
    print(f"\n{total - missing}/{total} biases have examples, {missing} still need examples")


if __name__ == "__main__":
    main()
