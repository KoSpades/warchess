"""Measured win rates, for the PvE bot's draft.

Generated, not hand-written. Produced by

    playtest.random_tournament(n=5000, team_size=4, seed=2)

under the AI as it stood at that run: 5000 matches, 448 draws, roster mean 50.0%.
Each hero played about 677 games, so the noise floor is around +/-4.6 points and
neighbouring entries are not really distinguishable from one another.

These measure heroes *as this AI plays them*, not the heroes themselves. The
tournament that produced them is in `balance.md`. Regenerate whenever `ai.py`
changes or a stat block moves, or the bot will be drafting to a table that no
longer describes the game.
"""

# hero key -> win rate, percent. Ordered best first.
WIN_RATE = {
    'snow_woman'        :  80.1,   # 雪女
    'thunder_dragon'    :  74.0,   # 雷霆龙
    'gunslinger'        :  70.5,   # 双枪手
    'cannoneer'         :  68.1,   # 炮手
    'druid'             :  67.8,   # 德鲁伊
    'forest_child'      :  64.8,   # 森林之子
    'fairy'             :  64.5,   # 妖精
    'robot'             :  61.8,   # 机器人
    'weapon_master'     :  61.6,   # 武器大师
    'sniper'            :  60.3,   # 狙击手
    'berserker'         :  59.1,   # 狂战士
    'water_mage'        :  58.4,   # 水法师
    'cursed_doll'       :  57.9,   # 诅咒娃娃
    'astrologer'        :  57.5,   # 占星师
    'four_beasts'       :  55.9,   # 四圣兽
    'goblin_gang'       :  55.4,   # 哥布林团伙
    'mist_lady'         :  54.6,   # 雾女
    'snake_emperor'     :  54.5,   # 蛇帝
    'barbarian_king'    :  53.7,   # 蛮王
    'pope'              :  53.7,   # 教皇
    'woodcutter'        :  53.0,   # 樵夫
    'fire_mage'         :  52.3,   # 火法师
    'paladin'           :  52.2,   # 圣骑士
    'gatekeeper'        :  51.9,   # 门神
    'gargoyle'          :  51.3,   # 石像鬼
    'spearman'          :  51.3,   # 枪兵
    'imp'               :  51.1,   # 小鬼
    'arms_dealer'       :  50.4,   # 军火商人
    'werewolf'          :  48.3,   # 狼人
    'mars'              :  48.2,   # 马尔斯
    'wind_rider'        :  47.9,   # 御风使
    'swordsman'         :  47.4,   # 剑客
    'explorer'          :  47.0,   # 探险家
    'painter'           :  46.7,   # 画师
    'centaur'           :  46.4,   # 半人马
    'sabretooth'        :  45.3,   # 剑齿虎
    'mountain_god'      :  45.2,   # 山神
    'gunner'            :  44.9,   # 男枪
    'dream_goddess'     :  44.4,   # 美梦神
    'plague_doctor'     :  44.1,   # 鸟嘴医生
    'elder'             :  43.9,   # 长老
    'judge'             :  42.9,   # 法官
    'diver'             :  42.5,   # 潜水者
    'blood_mage'        :  42.1,   # 血魔法师
    'magician'          :  42.0,   # 魔术师
    'wanderer'          :  41.8,   # 浪子
    'world_tree'        :  41.7,   # 世界树
    'ghost'             :  41.3,   # 鬼魂
    'victory_goddess'   :  41.0,   # 胜利女神
    'artisan'           :  39.6,   # 工匠
    'fisherman'         :  38.7,   # 渔夫
    'hunter'            :  38.4,   # 猎人
    'tide_goddess'      :  38.0,   # 潮汐女神
    'shopkeeper'        :  37.8,   # 杂货店爷爷
    'bomber'            :  37.2,   # 炸弹客
    'mammoth'           :  37.0,   # 猛犸
    'minotaur'          :  36.8,   # 牛头
    'strongman'         :  35.5,   # 大力士
    'assassin'          :  30.9,   # 刺客
}

ROSTER_MEAN = 50.0


def win_rate(key):
    """What that card is worth to the bot. A hero added since the last tournament
    has no measurement yet and counts as exactly average rather than as worthless —
    an unmeasured card must not be one the bot refuses to ever pick."""
    return WIN_RATE.get(key, ROSTER_MEAN)
