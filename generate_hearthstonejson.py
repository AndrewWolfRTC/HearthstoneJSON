#!/usr/bin/env python
import json
import os
import sys
from argparse import ArgumentParser
from enum import IntEnum

from hearthstone.cardxml import load
from hearthstone.enums import CardType, Faction, GameTag, Locale
from hearthstone.utils import SCHEME_CARDS, SPELLSTONE_STRINGS

NBSP = "\u00A0"

MECHANICS_TAGS = [
	GameTag.ADJACENT_BUFF,
	GameTag.AI_MUST_PLAY,
	GameTag.AFFECTED_BY_SPELL_POWER,
	GameTag.APPEAR_FUNCTIONALLY_DEAD,
	GameTag.ADAPT,
	GameTag.AURA,
	GameTag.AUTOATTACK,
	GameTag.BATTLECRY,
	GameTag.CANT_ATTACK,
	GameTag.CANT_BE_DESTROYED,
	GameTag.CANT_BE_FATIGUED,
	GameTag.CANT_BE_SILENCED,
	GameTag.CANT_BE_TARGETED_BY_ABILITIES,
	GameTag.CANT_BE_TARGETED_BY_HERO_POWERS,
	GameTag.CHARGE,
	GameTag.CHOOSE_ONE,
	GameTag.COLLECTIONMANAGER_FILTER_MANA_EVEN,
	GameTag.COLLECTIONMANAGER_FILTER_MANA_ODD,
	GameTag.COMBO,
	GameTag.COUNTER,
	GameTag.DEATH_KNIGHT,
	GameTag.DEATHRATTLE,
	GameTag.DISCOVER,
	GameTag.DIVINE_SHIELD,
	GameTag.DUNGEON_PASSIVE_BUFF,
	GameTag.ENCHANTMENT_INVISIBLE,
	GameTag.ECHO,
	GameTag.ENRAGED,
	GameTag.EVIL_GLOW,
	GameTag.FINISH_ATTACK_SPELL_ON_DAMAGE,
	GameTag.FORGETFUL,
	GameTag.FREEZE,
	GameTag.GEARS,
	GameTag.GHOSTLY,
	GameTag.GRIMY_GOONS,
	GameTag.HEROPOWER_DAMAGE,
	GameTag.IGNORE_HIDE_STATS_FOR_BIG_CARD,
	GameTag.IMMUNE,
	GameTag.INSPIRE,
	GameTag.JADE_GOLEM,
	GameTag.JADE_LOTUS,
	GameTag.KABAL,
	GameTag.LIFESTEAL,
	GameTag.MODULAR,
	GameTag.MULTIPLY_BUFF_VALUE,
	GameTag.MORPH,
	GameTag.OVERKILL,
	GameTag.OVERLOAD,
	GameTag.POISONOUS,
	GameTag.PUZZLE,
	GameTag.QUEST,
	GameTag.RECEIVES_DOUBLE_SPELLDAMAGE_BONUS,
	GameTag.RECRUIT,
	GameTag.RITUAL,
	GameTag.RUSH,
	GameTag.SECRET,
	GameTag.SILENCE,
	GameTag.SPARE_PART,
	GameTag.SPELLPOWER,
	GameTag.START_OF_GAME,
	GameTag.STEALTH,
	GameTag.SUMMONED,
	GameTag.TAG_ONE_TURN_EFFECT,
	GameTag.TAUNT,
	GameTag.TOPDECK,
	GameTag.TRIGGER_VISUAL,
	GameTag.TWINSPELL,
	GameTag.UNTOUCHABLE,
	GameTag.WINDFURY,
	GameTag.ImmuneToSpellpower,
	GameTag.InvisibleDeathrattle,
]


def json_dump(obj, filename, pretty=False):
	print("Writing to %r" % (filename))
	kwargs = {
		"ensure_ascii": False,
		"separators": (",", ":"),
		"sort_keys": True,
	}
	if pretty:
		kwargs["indent"] = "\t"
		kwargs["separators"] = (",", ": ")

	with open(filename, "w", encoding="utf8") as f:
		json.dump(obj, f, **kwargs)


def show_field(card, k, v):
	if k == "cost" and card.type not in (CardType.ENCHANTMENT, CardType.HERO):
		return True
	if k == "faction" and v == Faction.NEUTRAL:
		return False
	if k == "attack" and card.type in (CardType.MINION, CardType.WEAPON):
		return True
	if k == "health" and card.type in (CardType.MINION, CardType.HERO):
		return True
	if k == "durability" and card.type == CardType.WEAPON:
		return True
	return bool(v)


def get_tags(card):
	tags, referenced_tags = [], []
	for gametag in MECHANICS_TAGS:
		tag = card.tags.get(gametag, 0)
		if tag:
			tags.append(gametag.name)

		referenced_tag = card.referenced_tags.get(gametag, 0)
		if referenced_tag:
			referenced_tags.append(gametag.name)

	return tags, referenced_tags


def clean_card_description(text, card):
	card_id = card.id
	text = text.replace("_", NBSP)
	count = text.count("@")

	if not count:
		return text, ""

	if card_id in SPELLSTONE_STRINGS:
		return text, text.replace("@", "")
	elif card_id in SCHEME_CARDS:

		# Rise of Shadows schemes use '@' as a variable / placeholder, with a defaut value
		# populated from a data tag.

		num = card.tags.get(GameTag.TAG_SCRIPT_DATA_NUM_1, 0)
		return text, text.replace("@", str(num), 1)

	parts = text.split("@")
	if len(parts) == 2:
		text, collection_text = parts
		text = text.strip()
	elif len(parts) > 2:
		collection_text = parts[0]
	return text, collection_text


def serialize_card(card):
	text, collection_text = clean_card_description(card.description, card)

	ret = {
		"id": card.card_id,
		"dbfId": card.dbf_id,
		"name": card.name,
		"flavor": card.flavortext,
		"text": text,
		"collectionText": collection_text,
		"howToEarn": card.how_to_earn,
		"howToEarnGolden": card.how_to_earn_golden,
		"targetingArrowText": card.targeting_arrow_text,
		"artist": card.artist,
		"faction": card.faction,
		"hideStats": card.hide_stats,
		"cardClass": card.card_class,
		"race": card.race,
		"rarity": card.rarity,
		"set": card.card_set,
		"type": card.type,
		"collectible": card.collectible,
		"elite": card.elite,
		"attack": card.atk,
		"cost": card.cost,
		"armor": card.armor,
		"durability": card.durability,
		"health": card.health,
		"overload": card.overload,
		"puzzleType": card.tags.get(GameTag.PUZZLE_TYPE, 0),
		"questReward": card.quest_reward,
		"spellDamage": card.spell_damage,
	}
	ret = {k: v for k, v in ret.items() if show_field(card, k, v)}

	for k, v in ret.items():
		if isinstance(v, IntEnum):
			ret[k] = v.name

	tags, referenced_tags = get_tags(card)
	if tags:
		ret["mechanics"] = tags

	if referenced_tags:
		ret["referencedTags"] = referenced_tags

	if card.entourage:
		ret["entourage"] = card.entourage

	if card.multiple_classes:
		ret["classes"] = [c.name for c in card.classes]

	if card.multi_class_group:
		ret["multiClassGroup"] = card.multi_class_group.name

	if card.requirements:
		ret["playRequirements"] = {
			k.name: v for k, v in card.requirements.items()
			if hasattr(k, "name")
		}

	return ret


def export_cards_to_file(cards, filename, locale):
	ret = []
	for card in cards:
		card.locale = locale
		ret.append(serialize_card(card))

	json_dump(ret, filename)


def export_all_locales_cards_to_file(cards, filename):
	tag_names = {
		GameTag.CARDNAME: "name",
		GameTag.FLAVORTEXT: "flavor",
		GameTag.CARDTEXT_INHAND: "text",
		GameTag.HOW_TO_EARN: "howToEarn",
		GameTag.HOW_TO_EARN_GOLDEN: "howToEarnGolden",
		GameTag.TARGETING_ARROW_TEXT: "targetingArrowText",
	}
	ret = []

	for card in cards:
		obj = serialize_card(card)
		obj["collectionText"] = {}
		for tag, key in tag_names.items():
			value = card.strings.get(tag, {})
			if key == "text":
				for locale, localized_value in value.items():
					text, collection_text = clean_card_description(localized_value, card)
					value[locale] = text
					if collection_text:
						obj["collectionText"][locale] = collection_text
			if value:
				obj[key] = value

		if not obj["collectionText"]:
			del obj["collectionText"]

		ret.append(obj)

	json_dump(ret, filename)


def main():
	parser = ArgumentParser()
	parser.add_argument(
		"-o", "--output-dir",
		type=str,
		dest="output_dir",
		default="out",
		help="Output directory"
	)
	parser.add_argument(
		"-i", "--input-dir",
		type=str,
		required=True,
		dest="input_dir",
		help="Input hsdata directory"
	)
	parser.add_argument("--locale", type=str, nargs="*", help="Only generate one locale")
	args = parser.parse_args(sys.argv[1:])

	db, xml = load(os.path.join(args.input_dir, "CardDefs.xml"))

	cards = db.values()
	collectible_cards = [card for card in cards if card.collectible]

	filter_locales = [loc.lower() for loc in args.locale or []]

	for locale in Locale:
		if locale.unused:
			continue

		if filter_locales and locale.name.lower() not in filter_locales:
			continue

		basedir = os.path.join(args.output_dir, locale.name)
		if not os.path.exists(basedir):
			os.makedirs(basedir)

		filename = os.path.join(basedir, "cards.json")
		export_cards_to_file(cards, filename, locale.name)

		filename = os.path.join(basedir, "cards.collectible.json")
		export_cards_to_file(collectible_cards, filename, locale.name)

	# Generate merged locales
	if "all" in filter_locales or not filter_locales:
		basedir = os.path.join(args.output_dir, "all")
		if not os.path.exists(basedir):
			os.makedirs(basedir)
		filename = os.path.join(basedir, "cards.json")
		export_all_locales_cards_to_file(cards, filename)
		filename = os.path.join(basedir, "cards.collectible.json")
		export_all_locales_cards_to_file(collectible_cards, filename)


if __name__ == "__main__":
	main()
