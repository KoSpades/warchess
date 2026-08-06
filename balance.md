# Balance — 5000 random 4v4 matches

`playtest.random_tournament(n=5000, team_size=4, seed=2)` under the current AI.
448 draws (9.0%), roster mean 50.0%.

Bar is the win rate above a 25% floor, one block per 2 points.

```
  S  70%+   (3 heroes)
    1 雪女        80.1%  ████████████████████████████
    2 雷霆龙      74.0%  ████████████████████████
    3 双枪手      70.5%  ███████████████████████

  A  60-70%   (7 heroes)
    4 炮手        68.1%  ██████████████████████
    5 德鲁伊      67.8%  █████████████████████
    6 森林之子    64.8%  ████████████████████
    7 妖精        64.5%  ████████████████████
    8 机器人      61.8%  ██████████████████
    9 武器大师    61.6%  ██████████████████
   10 狙击手      60.3%  ██████████████████

  B  50-60%   (18 heroes)
   11 狂战士      59.1%  █████████████████
   12 水法师      58.4%  █████████████████
   13 诅咒娃娃    57.9%  ████████████████
   14 占星师      57.5%  ████████████████
   15 四圣兽      55.9%  ███████████████
   16 哥布林团伙  55.4%  ███████████████
   17 雾女        54.6%  ███████████████
   18 蛇帝        54.5%  ███████████████
   19 蛮王        53.7%  ██████████████
   20 教皇        53.7%  ██████████████
   21 樵夫        53.0%  ██████████████
   22 火法师      52.3%  ██████████████
   23 圣骑士      52.2%  ██████████████
   24 门神        51.9%  █████████████
   25 石像鬼      51.3%  █████████████
   26 枪兵        51.3%  █████████████
   27 小鬼        51.1%  █████████████
   28 军火商人    50.4%  █████████████

  C  45-50%   (9 heroes)
   29 狼人        48.3%  ████████████
   30 马尔斯      48.2%  ████████████
   31 御风使      47.9%  ███████████
   32 剑客        47.4%  ███████████
   33 探险家      47.0%  ███████████
   34 画师        46.7%  ███████████
   35 半人马      46.4%  ███████████
   36 剑齿虎      45.3%  ██████████
   37 山神        45.2%  ██████████

  D  40-45%   (12 heroes)
   38 男枪        44.9%  ██████████
   39 美梦神      44.4%  ██████████
   40 鸟嘴医生    44.1%  ██████████
   41 长老        43.9%  █████████
   42 法官        42.9%  █████████
   43 潜水者      42.5%  █████████
   44 血魔法师    42.1%  █████████
   45 魔术师      42.0%  █████████
   46 浪子        41.8%  ████████
   47 世界树      41.7%  ████████
   48 鬼魂        41.3%  ████████
   49 胜利女神    41.0%  ████████

  F  under 40%   (10 heroes)
   50 工匠        39.6%  ███████
   51 渔夫        38.7%  ███████
   52 猎人        38.4%  ███████
   53 潮汐女神    38.0%  ███████
   54 杂货店爷爷  37.8%  ██████
   55 炸弹客      37.2%  ██████
   56 猛犸        37.0%  ██████
   57 牛头        36.8%  ██████
   58 大力士      35.5%  █████
   59 刺客        30.9%  ███
```

These are **not** intrinsic hero strengths — they measure a hero *as played by
this AI*, which has known blind spots (see the focus-fire notes in `ai.py`).
Re-run and diff after any change to `ai.py` or to a stat block, and regenerate
`winrates.py` with it: the PvE bot drafts from that table.

Noise floor is about +/-4.6pp per hero at this sample size (~677 games each);
treat any smaller movement as nothing.

## Reach still predicts win rate

| reach | heroes | mean win rate |
|---|---|---|
| 1 (must touch) | 9 | 43.8% |
| 2-3 (short) | 30 | 47.2% |
| 4-7 | 14 | 57.5% |
| 8 (whole board) | 6 | 56.4% |

`r = +0.46` across the roster. That is an AI artifact, not a balance fact: an AI
that fights at range and cannot plan a kill more than one turn out makes short
reach look like a weakness.
