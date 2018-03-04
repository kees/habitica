#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Phil Adams http://philadams.net

habitica: commandline interface for http://habitica.com
http://github.com/philadams/habitica

TODO:philadams add logging to .api
TODO:philadams get logger named, like requests!
"""


from bisect import bisect
import json
import logging
import netrc
import os.path
import random
import sys
from operator import itemgetter
from re import finditer
from time import sleep, time
from webbrowser import open_new_tab

from docopt import docopt

from . import api

from pprint import pprint

try:
    import ConfigParser as configparser
except:
    import configparser


VERSION = 'habitica version 0.0.12'
TASK_VALUE_BASE = 0.9747  # http://habitica.wikia.com/wiki/Task_Value
HABITICA_REQUEST_WAIT_TIME = 0.5  # time to pause between concurrent requests
HABITICA_TASKS_PAGE = '/#/tasks'
# https://trello.com/c/4C8w1z5h/17-task-difficulty-settings-v2-priority-multiplier
PRIORITY = {'easy': 1,
            'medium': 1.5,
            'hard': 2}
AUTH_CONF = os.path.expanduser('~') + '/.config/habitica/auth.cfg'
CACHE_CONF = os.path.expanduser('~') + '/.config/habitica/cache.cfg'
SETTINGS_CONF = os.path.expanduser('~') + '/.config/habitica/settings.cfg'

SECTION_HABITICA = 'Habitica'
SECTION_CACHE_QUEST = 'Quest'

FOOD_MAP = {
    'Saddle':           'ignore',
    'Meat':             'Base',
    'CottonCandyBlue':  'CottonCandyBlue',
    'CottonCandyPink':  'CottonCandyPink',
    'Honey':            'Golden',
    'Milk':             'White',
    'Strawberry':       'Red',
    'Chocolate':        'Shade',
    'Fish':             'Skeleton',
    'Potatoe':          'Desert',
    'RottenMeat':       'Zombie',
}

def load_typo_check(config, defaults, section, configfile):
    for item in config.options(section):
        if item not in defaults:
            raise ValueError("Option '%s' (section '%s') in '%s' not known!"
                             % (item, section, configfile))

def load_settings(configfile):
    """Get settings data from the SETTINGS_CONF file."""

    logging.debug('Loading habitica settings data from %s' % configfile)

    integers = {'sell-max': "-1",
                'sell-reserved': "-1",
                'eggs-extra': "0",
               }
    strings = { }
    defaults = integers.copy()
    defaults.update(strings)

    config = configparser.SafeConfigParser(defaults)
    config.read(configfile)

    if not config.has_section(SECTION_HABITICA):
        config.add_section(SECTION_HABITICA)

    load_typo_check(config, defaults, SECTION_HABITICA, configfile)

    settings = {}
    for item in integers:
        settings[item] = int(config.get(SECTION_HABITICA, item))
    for item in strings:
        settings[item] = config.get(SECTION_HABITICA, item)

    return settings


def load_auth(configfile):
    """Get authentication data from the AUTH_CONF file."""

    logging.debug('Loading habitica auth data from %s' % configfile)

    try:
        cf = open(configfile)
    except IOError:
        logging.error("Unable to find '%s'." % configfile)
        exit(1)

    config = configparser.SafeConfigParser()
    config.readfp(cf)

    cf.close()

    # Config name to authentication name mapping
    mapping = {'url': 'url',
               'login': 'x-api-user',
               'password': 'x-api-key'
              }

    # Get data from config
    rv = {}
    try:
        for item in mapping:
            rv[mapping[item]] = config.get(SECTION_HABITICA, item)

    except configparser.NoSectionError:
        logging.error("No '%s' section in '%s'" % (SECTION_HABITICA,
                                                   configfile))
        exit(1)

    except configparser.NoOptionError as e:
        logging.error("Missing option in auth file '%s': %s"
                      % (configfile, e.message))
        exit(1)

    # Do this after checking for the section.
    load_typo_check(config, mapping, SECTION_HABITICA, configfile)

    # Return auth data as a dictionnary
    return rv


def load_cache(configfile):
    logging.debug('Loading cached config data (%s)...' % configfile)

    defaults = {'quest_key': '',
                'quest_s': 'Not currently on a quest'}

    cache = configparser.SafeConfigParser(defaults)
    cache.read(configfile)

    if not cache.has_section(SECTION_CACHE_QUEST):
        cache.add_section(SECTION_CACHE_QUEST)

    return cache


def update_quest_cache(configfile, **kwargs):
    logging.debug('Updating (and caching) config data (%s)...' % configfile)

    cache = load_cache(configfile)

    for key, val in kwargs.items():
        cache.set(SECTION_CACHE_QUEST, key, val)

    with open(configfile, 'wb') as f:
        cache.write(f)

    cache.read(configfile)

    return cache


def get_task_ids(tids):
    """
    handle task-id formats such as:
        habitica todos done 3
        habitica todos done 1,2,3
        habitica todos done 2 3
        habitica todos done 1-3,4 8
    tids is a seq like (last example above) ('1-3,4' '8')
    """
    logging.debug('raw task ids: %s' % tids)
    task_ids = []
    for raw_arg in tids:
        for bit in raw_arg.split(','):
            if '-' in bit:
                start, stop = [int(e) for e in bit.split('-')]
                task_ids.extend(range(start, stop + 1))
            else:
                task_ids.append(int(bit))
    return [e - 1 for e in set(task_ids)]


def nice_name(thing):
    if '_' in thing:
        thing = thing.replace('_', '-')
    prettied = " ".join(thing.split('-')[::-1])
    # split camel cased words
    matches = finditer('.+?(?:(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|$)',
                        prettied)
    prettier = ' '.join([m.group(0).title() for m in matches])
    return prettier


def find_pet_to_feed(pets, items, suffix, finicky):
    basic = [ 'BearCub', 'Cactus', 'Dragon', 'FlyingPig',
              'Fox', 'LionCub', 'PandaCub', 'TigerCub', 'Wolf' ]
    rare = [ 'Wolf-Veteran', 'Wolf-Cerberus', 'Dragon-Hydra',
             'Turkey-Base', 'BearCub-Polar', 'MantisShrimp-Base',
             'JackOLantern-Base', 'Mammoth-Base', 'Tiger-Veteran',
             'Phoenix-Base', 'Turkey-Gilded',
             'JackOLantern-Ghost', 'Jackalope-RoyalPurple' ]

    mouth = None
    best = 0

    for pet in pets:
        fed = items['pets'][pet]

        # Unhatched pet.
        if fed <= 0:
            # print("Unhatched: %s" % (pet))
            continue
        # Unfeedable pet.
        if pet in rare:
            continue
        if items['mounts'].get(pet, 0) == 1 and fed == 5:
            # print("Has mount: %s" % (pet))
            continue
        # Not best food match.
        if finicky and not pet.endswith('-%s' % (suffix)):
            # print("Not a match for %s: %s" % (suffix, pet))
            continue

        # Feed the pet that is closest to becoming a mount.
        if fed > best:
            best = fed
            mouth = pet
        elif fed == best:
            # In the case of a tie, prefer feeding basic pets
            # to get Pet achievement.
            if pet in basic:
                mouth = pet
    return mouth

def updated_task_list(tasks, tids):
    for tid in sorted(tids, reverse=True):
        del(tasks[tid])
    return tasks


def print_task_list(tasks):
    for i, task in enumerate(tasks):
        completed = 'x' if task['completed'] else ' '
        print('[%s] %s %s' % (completed, i + 1, task['text'].encode('utf8')))


def qualitative_task_score_from_value(value):
    # task value/score info: http://habitica.wikia.com/wiki/Task_Value
    scores = ['*', '**', '***', '****', '*****', '******', '*******']
    breakpoints = [-20, -10, -1, 1, 5, 10]
    return scores[bisect(breakpoints, value)]

def get_currency(gp, balance="0.0"):
    gem = int(float(balance) * 4)
    gp = float(gp)
    gold = int(gp)
    silver = int((gp - int(gp)) * 100)
    report = ''
    if gem > 0:
        report += '%d Gem%s, ' % (gem, "" if gem == 1 else "s")
    report += '%d Gold' % (gold)
    if silver > 0:
        report += ', %d Silver' % (silver)
    return report

max_report = { 'exp': {'title':'Experience', 'max':'toNextLevel',
                                             'maxValue': "0"},
               'hp':  {'title':'Health', 'max':'maxHealth',
                                         'maxValue': "50"},
               'mp':  {'title':'Mana', 'max':'maxMP',
                                       'maxValue': "100"},
             }

# XXX: This is a hack to refresh the current stats to find maxes,
# which are sometimes missing for some reason.
def fix_max(hbt, item, bstats, astats, refresh=True):
    if astats.get(max_report[item]['max'], None) == None:
        # If max exists in "before" stats, use it instead.
        if bstats.get(max_report[item]['max'], None) != None:
            astats[max_report[item]['max']] = bstats[max_report[item]['max']]
        elif refresh and item != 'hp':
            # Perform full refresh and update all report items.
            refresh = hbt.user()
            rstats = refresh.get('stats', [])
            for fixup in max_report:
                astats[max_report[fixup]['max']] = rstats[max_report[fixup]['max']]
        else:
            # Either no refresh wanted, or max HP is missing which is static.
            astats[max_report[item]['max']] = max_report[item]['maxValue']
    return astats

def show_delta(hbt, before, after):
    bstats = before.get('stats', [])
    astats = after.get('stats', [])
    bitems = before.get('items', [])
    aitems = after.get('items', [])

    for item in max_report:
        delta = int(astats[item] - bstats[item])
        if delta != 0:
            # XXX: hack to fix max entry.
            astats = fix_max(hbt, item, bstats, astats)

            print('%s: %d (%d/%d)' % (max_report[item]['title'],
                                      delta, int(astats[item]),
                                      int(astats.get(max_report[item]['max'],
                                                     "0"))))

    # Currency
    bgp = float(bstats.get('gp', "0.0"))
    agp = float(astats.get('gp', "0.0"))
    gp = agp - bgp
    bgems = float(before.get('balance', "0.0"))
    agems = float(after.get('balance', "0.0"))
    gems = agems - bgems
    if gp != 0.0 or gems != 0.0:
        print("%s" % (get_currency(gp, gems)))

    # Pets
    apets = aitems['pets']
    bpets = bitems['pets']
    for pet in apets:
        if bpets.get(pet, 0) <= 0 and apets[pet] > 0:
            print("Hatched %s" % (nice_name(pet)))

    # Food
    afood = aitems['food']
    bfood = bitems['food']
    for food in afood:
        if afood.get(food, 0) > bfood.get(food, 0):
            print("Received %s" % (nice_name(food)))

    # Mounts
    amounts = aitems['mounts']
    bmounts = bitems['mounts']
    for mount in amounts:
        if bmounts.get(mount, '') != amounts[mount] and amounts[mount] > 0:
            print("Metamorphosed a %s" % (nice_name(mount)))

    # Equipment
    bequip = bitems['gear']['equipped']
    aequip = aitems['gear']['equipped']
    for location, item in aequip.iteritems():
        if bequip.get(location, '') != item:
            print("%s now has %s" % (location, item))


def do_item_enumerate(user, requested, ordered=False, pretty=True):
    counted = False
    items = user.get('items', [])
    if len(requested) == 0:
        for item in items:
            # Attempt to figure out if this is a dict of dicts or not.
            try:
                if isinstance(items[item], dict):
                    one = items[item].keys()[0]
                    if isinstance(items[item][one], dict):
                        for thing in items[item]:
                            print('%s/%s' % (item, thing))
                        continue
            except:
                pass
            print('%s' % (item))
        return

    results = {}
    for name in requested:
        if '/' in name:
            main, sub = name.split('/', 1)
            items = items.get(main, {sub: []})
            name = sub

        available = items.get(name, [])
        #print(available)
        if len(available) == 0:
            print("You don't have any %s!" % (name))
            continue
        if isinstance(available, unicode):
            # This is a singleton, so disable counting.
            counted = False
            if pretty:
                result_name = nice_name(available)
            else:
                result_name = available
            results[result_name] = 1
        elif isinstance(available, dict):
            for item in available:
                if pretty:
                    result_name = nice_name(item)
                else:
                    result_name = item

                if isinstance(items[name][item], bool):
                    # If false, we may want to skip it...
                    #continue
                    value = True
                elif isinstance(items[name][item], unicode):
                    result_name += ": %s" % (items[name][item])
                    value = True
                elif isinstance(items[name][item], int):
                    # This is an integer item, so count them.
                    counted = True
                    value = items[name][item]
                else:
                    # Include unknown type in results.
                    result_name += " %s" % (str(type(items[name][item])))
                    value = True

                if value:
                    results[result_name] = value
        else:
            print("Don't know how to show %s" % (str(type(available))))
            sys.exit(1)

    if counted:
        if ordered:
            for i, c in sorted(results.items(), key=itemgetter(1)):
                print('%s: %d' % (i, c))
        else:
            for item in results:
                print('%s: %d' % (item, results[item]))
    else:
        if ordered:
            for i, c in sorted(results.items(), key=itemgetter(0)):
                print('%s' % (i))
        else:
            for item in results:
                print('%s' % (item))

def get_members(auth, party):
    result = []
    group = api.Habitica(auth=auth, resource="groups", aspect=party['id'])
    members = group(_one='members')
    for i in members:
        member = api.Habitica(auth=auth, resource="members", aspect=i['id'])()
        result.append(member)
    return result

def stat_down(hbt, user, stat, amount):
    stats = user.get('stats', [])
    stats = fix_max(hbt, stat, stats, stats, refresh=False)
    down = int(stats.get(max_report[stat]['max'],"0")) - int(stats[stat])
    print("%s has %d/%d %s" % (user['profile']['name'], int(stats[stat]),
                               int(stats[max_report[stat]['max']]),
                               max_report[stat]['title']))
    if down >= amount:
        return True
    return False

def party_hp_down_ten(auth, hbt, user, party=None, myself=False):
    needs_healing = False
    down = False
    if party == None:
        party = hbt.groups.party()
    if not myself:
        members = get_members(auth, party)
    else:
        members = [user]
    for member in members:
        if stat_down(hbt, member, 'hp', 10):
            print("%s needs healing" % (member['profile']['name']))
            needs_healing = True
    if needs_healing:
        return

    print("Already in good health!")
    sys.exit(1)

def hp_down_ten(auth, hbt, user):
    # Do a party check, but just a party of myself.
    party_hp_down_ten(hbt, user, myself=True)

def get_quest_info(cache, hbt, user=None, party=None):
    if user == None:
        user = hbt.user()
    if party == None:
        party = hbt.groups.party()
    # gather quest progress information (yes, janky. the API
    # doesn't make this stat particularly easy to grab...).
    # because hitting /content downloads a crapload of stuff, we
    # cache info about the current quest in cache.
    quest = 'Not currently on a quest'
    if (party is not None and
            party.get('quest', '') and
            party.get('quest').get('active')):

        quest_key = party['quest']['key']
        # wtf‽
        # party['quest']['progress'] != user['party']['quest']['progress']
        quest_damage = user['party']['quest']['progress']['up']
        collect_quest = {}

        if cache.get(SECTION_CACHE_QUEST, 'quest_key') != quest_key:
            # we're on a new quest, update quest key
            logging.info('Updating quest information...')
            content = hbt.content()
            quest_type = ''
            quest_max = []
            quest_title = content['quests'][quest_key]['text']

            # if there's a content/quests/<quest_key>/collect,
            # then drill into .../collect/<whatever>/count and
            # .../collect/<whatever>/text and get those values
            if content.get('quests', {}).get(quest_key, {}).get('collect'):
                logging.debug("\tOn a collection type of quest")
                quest_type = 'collect'
                for k, v in content['quests'][quest_key]['collect'].iteritems():
                    if k not in collect_quest.keys():
                        collect_quest[k] = {}
                    collect_quest[k]['max'] = v['count']
                    quest_max.append(str(v['count']))
            # else if it's a boss, then hit up
            # content/quests/<quest_key>/boss/hp
            elif content.get('quests', {}).get(quest_key, {}).get('boss'):
                logging.debug("\tOn a boss/hp type of quest")
                quest_type = 'hp'
                quest_max.append(str(content['quests'][quest_key]['boss']['hp']))
            # store repr of quest info from /content
            cache = update_quest_cache(CACHE_CONF,
                                       quest_key=str(quest_key),
                                       quest_type=str(quest_type),
                                       quest_max=' '.join(quest_max),
                                       quest_title=str(quest_title))

        # now we use /party and quest_type to figure out our progress!
        quest_type = cache.get(SECTION_CACHE_QUEST, 'quest_type')
        quest_progress = []
        quest = '"%s"' % (cache.get(SECTION_CACHE_QUEST, 'quest_title'))
        if quest_type == 'collect':
            qp_tmp = party['quest']['progress']['collect']
            # For some quests you collect multiple types of things.
            for k, v in qp_tmp.iteritems():
                quest_progress.append('%s: %s' % (nice_name(k), v))
                if k not in collect_quest.keys():
                    collect_quest[k] = {}
                collect_quest[k]['total'] = v
            for k, v in user['party']['quest']['progress']['collect'].iteritems():
                collect_quest[k]['current']  = v
            count = 0
            for k, v in collect_quest.iteritems():
                quest += ' %s %d/%d' % (nice_name(k), collect_quest[k]['total'],
                                        int(cache.get(SECTION_CACHE_QUEST, 'quest_max').split(' ')[count]))
                # If somebody is in the Inn, they will not have collected
                # anything.
                if k not in collect_quest or 'current' not in collect_quest[k]:
                    quest += ' (+0)'
                else:
                    quest += ' (+%d)' % (collect_quest[k]['current'])
                count += 1
        else:
            quest_progress.append('%d' % party['quest']['progress']['hp'])
            quest += ' %s/%s' % (' '.join(quest_progress),
                                 cache.get(SECTION_CACHE_QUEST, 'quest_max'))
            quest += ' (-%d)' % quest_damage

    return quest

def cli():
    """Habitica command-line interface.

  Usage: habitica [--version] [--help]
                  <command> [<args>...] [--difficulty=<d>]
                  [--verbose | --debug]

  Options:
    -h --help         Show this screen
    --version         Show version
    --difficulty=<d>  (easy | medium | hard) [default: easy]
    --verbose         Show some logging information
    --debug           Some all logging information

  The habitica commands are:
    status                     Show HP, XP, GP, and more
    status health              Show current health
    status mana                Show current mana
    habits                     List habit tasks
    habits up <task-id>        Up (+) habit <task-id>
    habits down <task-id>      Down (-) habit <task-id>
    dailies                    List daily tasks
    dailies done               Mark daily <task-id> complete
    dailies undo               Mark daily <task-id> incomplete
    todos                      List todo tasks
    todos done <task-id>       Mark one or more todo <task-id> completed
    todos add <task>           Add todo with description <task>
    server                     Show status of Habitica service
    home                       Open tasks page in default browser
    item                       Show list of item types
    item <type>                Show all items of given <type>
    feed                       Feed all food to matching pets
    hatch                      Use potions to hatch eggs, sell unneeded eggs
    sell                       Show list of all potions
    sell all [<max>]           Sell all hatching potions (up to <max> many)
    sell <type> [<max>]        Sell all <type> hatching potions (up to <max>)
    cast                       Show list of castable spells
    cast <spell> [<id>]        Cast <spell> (on task <id>)
    cast smart <spell> [<id>]  After smart-check, cast <spell> (on task <id>)
    gems                       Buy gems until you can't
    armoire                    Buy something from the armoire
    armoire empty              Empty the armoire of equipment
    buy-health                 Buy a health potion
    mystery-item               Open the mystery item box
    walk                       List available pets to walk
    walk <pet>                 Walk (equip) the <pet> pet
    walk random                Walk (equip) a random pet
    ride                       List available mounts
    ride <mount>               Ride (equip) the <mount> mount
    ride random                Ride (equip) a random mount
    equip <gear>               Equip a piece of gear
    sleep                      Rest in the inn
    arise                      Check out of the inn
    quest                      Report quest details
    quest accept               Accept a quest proposal

  For `habits up|down`, `dailies done|undo`, and `todos done`, you can pass
  one or more <task-id> parameters, using either comma-separated lists or
  ranges or both. For example, `todos done 1,3,6-9,11`.
  """

    # set up args
    args = docopt(cli.__doc__, version=VERSION)

    # set up logging
    if args['--verbose']:
        logging.basicConfig(level=logging.INFO)
    if args['--debug']:
        logging.basicConfig(level=logging.DEBUG)

    logging.debug('Command line args: {%s}' %
                  ', '.join("'%s': '%s'" % (k, v) for k, v in args.items()))

    # list of kinds of pets/potions (disregarding Magic Potion ones)
    kinds = [ 'Base', 'CottonCandyBlue', 'CottonCandyPink', 'Golden',
              'White', 'Red', 'Shade', 'Skeleton', 'Desert', 'Zombie' ]

    # Set up auth
    auth = load_auth(AUTH_CONF)

    # Prepare cache
    cache = load_cache(CACHE_CONF)

    # Load settings
    settings = load_settings(SETTINGS_CONF)

    # instantiate api service
    hbt = api.Habitica(auth=auth)

    # GET server status (v3 ok)
    if args['<command>'] == 'server':
        server = hbt.status()
        if server['status'] == 'up':
            print('Habitica server is up')
        else:
            print('Habitica server down... or your computer cannot connect')

    # open HABITICA_TASKS_PAGE (v3 ok)
    elif args['<command>'] == 'home':
        home_url = '%s%s' % (auth['url'], HABITICA_TASKS_PAGE)
        print('Opening %s' % home_url)
        open_new_tab(home_url)

    # GET item lists (v3 ok)
    elif args['<command>'] == 'item':
        user = hbt.user()
        do_item_enumerate(user, args['<args>'])

    # Feed all possible animals (v3 ok)
    elif args['<command>'] == 'feed':
        user = hbt.user()
        refreshed = True

        attempted_foods = set()
        fed_foods = set()

        while refreshed:
            refreshed = False
            items = user.get('items', [])
            foods = items['food']
            pets = items['pets']
            mounts = items['mounts']

            magic_pets = []
            for pet in pets:
                if pet.split('-')[1] in ['Spooky', 'Peppermint', 'Floral',
                        'Thunderstorm', 'Ghost', 'Cupid', 'Shimmer',
                        'Fairy', 'Floral', 'Aquatic', 'Ember', 'Holly',
                        'StarryNight', 'RoyalPurple']:
                    magic_pets.append(pet)

            for food in foods:
                # Handle seasonal foods that encode matching pet in name.
                if '_' in food:
                    best = food.split('_',1)[1]
                    if not food in FOOD_MAP:
                        FOOD_MAP[food] = best

                # Skip foods we don't have any of.
                if items['food'][food] <= 0:
                    continue

                # Find best pet to feed to.
                suffix = FOOD_MAP.get(food, None)
                if suffix == None:
                    print("Unknown food: %s" % (food))
                    continue
                if suffix == 'ignore':
                    continue

                # Track attempted foods
                attempted_foods.add(food)

                mouth = find_pet_to_feed(pets, items, suffix, True)

                # If we have food but its not ideal for pet, give it to a
                # magic pet which will eat anything.
                if not mouth:
                    mouth = find_pet_to_feed(magic_pets, items, suffix, False)
                    # print("Want to feed magic pet %s" % mouth)

                if mouth:
                    before = pets[mouth]

                    # if the less than ideal food is fed to a pet it's satiety
                    # increases by 1 not 5, so find the multiple of five.
                    satiety = int(5 * round(pets[mouth]/5))
                    # 50 is "fully fed and now a mount", 5 is best food growth
                    need_bites = bites = (50 - satiety) / 5
                    if items['food'][food] < bites:
                        bites = items['food'][food]

                    # Report how many more bites are needed before a mount.
                    moar = ""
                    if need_bites > bites:
                        need_bites -= bites
                        moar = " (needs %d more serving%s)" % (need_bites,
                                "" if need_bites == 1 else "s")

                    fed_foods.add(food)
                    print("Feeding %d %s to %s%s" % (bites, nice_name(food),
                                                   nice_name(mouth), moar))
                    before_user = user
                    feeder = api.Habitica(auth=auth, resource="user", aspect="feed")
                    for i in range(bites):
                        feeder(_method='post', _one=mouth, _two=food)
                    user = hbt.user()
                    show_delta(hbt, before_user, user)
                    refreshed = True
                    items = user.get('items', [])
                    pets = items['pets']
                    if pets[mouth] == before:
                        raise ValueError("failed to feed %s" % (mouth))
                    break

        for food in list(attempted_foods - fed_foods):
            print("Nobody wants to eat %i %s" % (items['food'][food], nice_name(food)))

    # Hatch all possible eggs (v3 ok)
    elif args['<command>'] == 'hatch':
        def no_zero_count(types):
            # don't include varieties of which we have 0
            result = {}
            for variety in types:
                if types[variety] != 0:
                    result[variety] = types[variety]
            return result
        def hatch_refresh(user):
            items = user.get('items', [])
            pets = items['pets']
            mounts = items['mounts']
            eggs = no_zero_count(items['eggs'])
            potions = no_zero_count(items['hatchingPotions'])
            foods = no_zero_count(items['food'])
            return (items, pets, mounts, eggs, potions, foods)

        user = hbt.user()
        refreshed = True

        while refreshed:
            refreshed = False
            items, pets, mounts, eggs, potions, foods = hatch_refresh(user)

            for egg in eggs:
                # dictionary with info re what we want to hatch
                # priority is the amount of food we have for the pet
                # -1 indicates we already have a mount
                #  0 is for pets for which we have no food
                # {priority: (potion, egg)}
                hatch = {}

                creatures = []
                for kind in kinds:
                    creatures.append('%s-%s' % (egg, kind))

                for creature in creatures:
                    # This pet is already hatched.
                    if pets.get(creature, 0) > 0:
                        continue
                    # We ran out of eggs.
                    if eggs.get(egg, 0) == 0:
                        continue

                    potion = creature.split('-')[-1]
                    # Missing the potion needed for this creature.
                    if potion not in potions:
                        print("Want to hatch a %s %s, but missing potion" %
                              (potion, egg))
                        continue
                    # already have a mount for this so new pet won't consume
                    # food
                    if items['mounts'].get(creature, 0) == 1:
                        hatch[-1] = (potion, egg)
                        continue
                    hungry_for= list(FOOD_MAP.keys())[list(FOOD_MAP.values()).index(potion)]
                    if hungry_for not in foods:
                        print("Want to hatch a %s %s, but have no food" %
                              (potion, egg))
                        hatch[0] = (potion, egg)
                        continue
                    stock = foods.get(hungry_for, 0)
                    if stock not in hatch:
                        hatch[stock] = (potion, egg)
                if not hatch:
                    continue
                for priority in sorted(hatch.keys(), reverse=True):
                    potion = hatch[priority][0]
                    egg = hatch[priority][1]
                    creature = '%s-%s' % (egg, potion)
                    print("Hatching a %s %s" % \
                          (nice_name(potion), nice_name(egg)))
                    before_user = user
                    hatcher = api.Habitica(auth=auth, resource="user", aspect="hatch")
                    hatcher(_method='post', _one=egg, _two=potion)
                    user = hbt.user()
                    show_delta(hbt, before_user, user)
                    refreshed = True
                    items, pets, mounts, eggs, potions, foods = hatch_refresh(user)
                    if pets.get(creature, 0) != 5:
                        raise ValueError("Failed to hatch %s" % (creature))
                    # we'll create a new priority matrix the next time around
                    break

        # How many eggs do we need for the future?
        tosell = []
        for egg in eggs:
            need_pets = []
            need_mounts = []

            # Don't bother reporting about eggs we have none of.
            if eggs[egg] == 0:
                continue

            creatures = []
            for kind in kinds:
                creatures.append('%s-%s' % (egg, kind))

            for creature in creatures:
                if mounts.get(creature, 0) == 0:
                    name = nice_name(creature.split('-',1)[1])
                    need_mounts.append(name)
                if pets.get(creature, 0) < 5:
                    need_pets.append(creature.split('-',1)[1])

            report = ""
            if len(need_pets):
                report += "%d Pet%s (%s)" % (len(need_pets),
                          "" if len(need_pets) == 1 else "s",
                          ", ".join(need_pets))
            if len(need_mounts):
                if len(report):
                    report += ", "
                report += "%d Mount%s (%s)" % (len(need_mounts),
                          "" if len(need_mounts) == 1 else "s",
                          ", ".join(need_mounts))
            if settings['eggs-extra']:
                if len(report):
                    report += ", "
                report += "%d extra" % (settings['eggs-extra'])

            need = len(need_pets) + len(need_mounts) + settings['eggs-extra']
            if need and need != settings['eggs-extra']:
                print("%s egg: Need %d for %s" % (nice_name(egg), need, report))

            # Sell unneeded eggs.
            sell = eggs[egg] - need
            if sell > 0:
                before = eggs[egg]
                print("Selling %d %s egg%s" % (sell, nice_name(egg),
                                               "" if sell == 1 else "s"))
                for i in range(sell):
                    tosell.append(egg)

        if len(tosell) > 0:
            before_user = user
            seller = api.Habitica(auth=auth, resource="user", aspect="sell")
            for i in range(len(tosell)):
                seller(_method='post', _one='eggs', _two=tosell[i])
            user = hbt.user()
            show_delta(hbt, before_user, user)

    # Sell all unneeded hatching potions (v3 ok)
    elif args['<command>'] == 'sell':
        sell_reserved = settings['sell-reserved']
        sell_max = settings['sell-max']
        if "max" in args['<args>']:
            arg = args['<args>'].index("max")
            name = args['<args>'].pop(arg)
            sell_max = int(args['<args>'].pop(arg))

        user = hbt.user()

        selling = args['<args>']
        if len(selling) == 0:
            do_item_enumerate(user, ['hatchingPotions'], ordered=True)
            sys.exit(0)

        if selling == ['all']:
            selling = kinds

        tosell = []
        items = user.get('items', [])
        stats = user.get('stats', [])
        potions = items['hatchingPotions']
        for sell in selling:
            if sell not in kinds:
                print("\"%s\" isn't a valid kind of potion." % (sell))
                sys.exit(1)
            if sell not in potions:
                print("You don't have any \"%s\"." % (sell))
                continue

            # Only sell potions above "sell-reserved" setting.
            if sell_reserved != -1:
                if potions[sell] < sell_reserved:
                    continue
                potions[sell] -= sell_reserved
            # Don't sell more than "sell-max" setting.
            if sell_max != -1 and potions[sell] > sell_max:
                potions[sell] = sell_max

            # Sell potions!
            if potions[sell] > 0:
                print("Selling %d %s potion%s" % (potions[sell],
                        nice_name(sell),
                        "" if potions[sell] == 1 else "s"))
                for i in range(potions[sell]):
                    tosell.append(sell)
        if len(tosell):
            before_user = user
            seller = api.Habitica(auth=auth, resource="user", aspect="sell")
            for i in range(len(tosell)):
                seller(_method='post', _one='hatchingPotions', _two=tosell[i])
            user = hbt.user()
            show_delta(hbt, before_user, user)

    # dump raw json for user (v3 ok)
    elif args['<command>'] == 'dump':
        user = None
        party = None
        items = None
        report = {}
        wanted = args['<args>']
        if len(wanted) == 0:
            wanted = ['user', 'party', 'members']

        # Fetch stuff we need for multiple targets.
        if 'user' in wanted or 'food' in wanted or 'pets' in wanted or 'mounts' in wanted:
            user = hbt.user()
        if 'food' in wanted or 'pets' in wanted or 'mounts' in wanted:
            items = user.get('items', [])
        if 'party' in wanted or 'members' in wanted:
            party = hbt.groups.party()

        # Add report details.
        if 'user' in wanted:
            report['user'] = user
        if 'party' in wanted:
            report['party'] = party
        if 'members' in wanted:
            group = api.Habitica(auth=auth, resource="groups", aspect=party['id'])
            report['members'] = group(_one='members')
        if 'food' in wanted:
            report['food'] = items['food']
        if 'pets' in wanted:
            report['pets'] = items['pets']
        if 'mounts' in wanted:
            report['mounts'] = items['mounts']
        if 'content' in wanted:
            report['content'] = hbt.content()

        # Dump the report.
        print(json.dumps(report, indent=4, sort_keys=True))

    # cast/skill on task/self/party (v3 ok)
    elif args['<command>'] == 'cast':
        user = hbt.user()
        stats = user.get('stats', '')
        uclass = stats['class']

        # class: {spell: target}
        spells = {'warrior': {'valorousPresence': 'party',
                              'defensiveStance': 'self',
                              'smash': 'task',
                              'intimidate': 'party'},
                  'rogue': {'pickPocket': 'task',
                            'backStab': 'task',
                            'toolsOfTrade': 'party',
                            'stealth': 'self'},
                  'wizard': {'fireball': 'task',
                             'mpheal': 'party',
                             'earth': 'party',
                             'frost': 'self'
                            },
                  'healer': {'heal': 'self',
                             'healAll': 'party',
                             'protectAura': 'party',
                             'brightness': 'self'
                            }
                 }

        smart = {'heal': hp_down_ten,
                 'healAll': party_hp_down_ten,
                }

        if len(args['<args>']) == 0:
            for spell in spells[uclass]:
                print("%s (%s)" % (spell, spells[uclass][spell]))
            sys.exit(0)

        spell = args['<args>'][0]

        precast = None
        if spell == "smart":
            spell = args['<args>'].pop(1)
            if spell not in smart:
                print("There's no smart way to cast that.")
                sys.exit(1)
            precast = smart[spell]

        if len(args['<args>']) == 2:
            task = args['<args>'][1]
        else:
            task = ''

        if spell not in spells[uclass]:
            print("That isn't a spell you know.")
            sys.exit(1)
        target = spells[uclass][spell]
        if target == 'task' and not task:
            print("You need to provide a task id to target.")
            sys.exit(1)

        # Do some smart checks before casting?
        if precast != None:
            precast(auth, hbt, user)

        # Report casting.
        msg = "Casting %s" % (spell)
        if target == 'party':
            msg += " on the party"
        elif target == 'task':
            msg += " on task %s" % (task)
        msg += "."
        print(msg)

        before_user = user
        charclass = api.Habitica(auth=auth, resource="user", aspect="class")
        if task != '':
            charclass(_method='post', _one='cast', _two=spell, targetId=task)
        else:
            charclass(_method='post', _one='cast', _two=spell)
        user = hbt.user()
        show_delta(hbt, before_user, user)

    # buy as many gems as possible (v3 ok)
    elif args['<command>'] == 'gems':
        user = hbt.user()
        before_user = user
        # base of 25 + (5 * (months subscribed / 3)) which seems to be
        # gemCapExtra
        # c.f. http://habitica.wikia.com/wiki/Gems
        gem_buy_limit = 25 + int(user['purchased']['plan']['consecutive']['gemCapExtra'])
        gems = gem_buy_limit - int(user['purchased']['plan']['gemsBought'])

        purchaser = api.Habitica(auth=auth, resource="user", aspect="purchase")
        for i in range(gems):
            purchaser(_method='post', _one='gems', _two='gem')
        user = hbt.user()
        show_delta(hbt, before_user, user)

    elif args['<command>'] == 'armoire':
        def do_purchase(empty=False):
            user = hbt.user()
            before_user = user
            # don't buy anything if it is already empty
            if empty and user['flags']['armoireEmpty']:
                return 'empty'
            purchase = api.Habitica(auth=auth, resource="user",
                                    aspect="buy-armoire")
            purchase(_method='post')
            user = hbt.user()
            show_delta(hbt, before_user, user)
            if user['flags']['armoireEmpty']:
                return 'empty'

        if args['<args>'] == ['empty']:
            while True:
                result = do_purchase(empty=True)
                if result == 'empty':
                    print("The armoire is now empty!")
                    break
        else:
            do_purchase()

    elif args['<command>'] == 'buy-health':
        user = hbt.user()
        before_user = user
        purchase = api.Habitica(auth=auth, resource="user",
                                aspect="buy-health-potion")
        purchase(_method='post')
        user = hbt.user()
        show_delta(hbt, before_user, user)

    elif args['<command>'] == 'mystery-item':
        user = hbt.user()
        before_user = user
        purchase = api.Habitica(auth=auth, resource="user",
                                aspect="open-mystery-item")
        purchase(_method='post')
        user = hbt.user()
        show_delta(hbt, before_user, user)

    # Quest manipulations
    elif args['<command>'] == 'quest':
        if len(args['<args>']) == 0:
            print(get_quest_info(cache, hbt))
        elif 'accept' in args['<args>']:
            user = hbt.user()
            party = hbt.groups.party()

            if party['quest']['active'] == True:
                if 'verbose' in args['<args>']:
                    print("Quest already started!")
            elif len(party['quest']['members']) == 0:
                if 'verbose' in args['<args>']:
                    print("No quest proposed!")
            elif party['quest']['members'][user['id']] != True:
                accepter = api.Habitica(auth=auth, resource="groups", aspect=party['id'])
                accepter(_method='post', _one='quests', _two='accept')
                print(get_quest_info(cache, hbt, user=user, party=party))
            else:
                if 'verbose' in args['<args>']:
                    print("Already accepted the quest!")
        else:
            print("Unknown quest argument '%s'" % (" ".join(args['<args>'])))
            sys.exit(1)

    # Select a pet or mount (v3 ok)
    elif args['<command>'] == 'ride' or args['<command>'] == 'walk':
        if args['<command>'] == 'ride':
            item_type = 'mounts'
            current = 'currentMount'
            name = 'mount'
            verb = 'riding'
        else:
            item_type = 'pets'
            current = 'currentPet'
            name = 'pet'
            verb = 'walking with'

        user = hbt.user()
        items = user.get('items', [])
        animals = items[item_type]

        if len(args['<args>']) == 0:
            do_item_enumerate(user, [item_type], ordered=True, pretty=False)
            return

        desired = "".join(args['<args>'])

        if desired.startswith('rand'):
            active = items.get(current, '')
            if active and len(animals) > 1:
                animals.pop(active)

            choice = random.randrange(0, len(animals)-1)
            chosen = animals.keys()[choice]
        else:
            if desired not in animals:
                print("You don't have a '%s' %s!" % (desired, name))
                sys.exit(1)
            chosen = desired

        equiper = batch = api.Habitica(auth=auth, resource="user", aspect="equip")
        equiper(_method='post', _one=name, _two=chosen)
        print("You are now %s a %s" % (verb, nice_name(chosen)))

    # equip a set of equipment (v3 ok)
    elif args['<command>'] == 'equip':
        equipping = args['<args>']
        user = hbt.user()
        before_user = user
        items = user.get('items', [])
        equipped = items['gear']['equipped']

        equiper = batch = api.Habitica(auth=auth, resource="user", aspect="equip")
        for equipment in equipping:
            equiper(_method='post', _one='equipped', _two=equipment)
        user = hbt.user()
        show_delta(hbt, before_user, user)

    # sleep/wake up (v3 ok)
    elif args['<command>'] == 'sleep' or args['<command>'] == 'arise':
        user = hbt.user()
        intent = args['<command>']
        sleeping = user['preferences']['sleep']
        if intent == 'sleep' and sleeping:
            print("You are already resting.")
            sys.exit(1)
        if not sleeping and intent == 'arise':
            print("You are already checked out.")
            sys.exit(1)

        sleeper = api.Habitica(auth=auth, resource="user", aspect="sleep")
        sleeper(_method='post')

    # GET user status (v3 ok)
    elif args['<command>'] == 'status':

        # gather status info
        user = hbt.user()
        party = hbt.groups.party()
        stats = user.get('stats', '')
        if args['<args>'] == ['mana']:
            print('%s' % int(stats['mp']))
            sys.exit(0)
        if args['<args>'] == ['health']:
            print('%s' % int(stats['hp']))
            sys.exit(0)
        items = user.get('items', '')
        sleeping = user['preferences']['sleep']
        food_count = sum(items['food'].values())
        egg_count = sum(items['eggs'].values())
        potion_count = sum(items['hatchingPotions'].values())

        quest = get_quest_info(cache, hbt, user, party)

        # prepare and print status strings
        title = 'Level %d %s' % (stats['lvl'], stats['class'].capitalize())
        if sleeping:
            title += ' (zZZz)'
        health = '%d/%d' % (stats['hp'], stats['maxHealth'])
        xp = '%d/%d' % (int(stats['exp']), stats['toNextLevel'])
        mana = '%d/%d' % (int(stats['mp']), stats['maxMP'])
        currency = get_currency(stats.get('gp', 0), user.get('balance', "0"))
        currentPet = items.get('currentPet', '')
        pet = '%s' % (currentPet)
        perishables = '%d serving%s, %d egg%s, %d potion%s' % \
                      (food_count, "" if food_count == 1 else "s",
                       egg_count, "" if egg_count == 1 else "s",
                       potion_count,  "" if potion_count == 1 else "s")
        mount = items.get('currentMount', '')

        members = get_members(auth, party)
        member_health = ', '.join(['%s: %d' % (i['profile']['name'], i['stats']['hp'])
                                   for i in members
                                   if i['profile']['name'] != user['profile']['name']])
        summary_items = ('health', 'xp', 'mana', 'currency', 'perishables',
                         'quest', 'pet', 'mount', 'party health')
        len_ljust = max(map(len, summary_items)) + 1
        print('-' * len(title))
        print(title)
        print('-' * len(title))
        print('%s %s' % ('Health:'.rjust(len_ljust, ' '), health))
        print('%s %s' % ('XP:'.rjust(len_ljust, ' '), xp))
        print('%s %s' % ('Mana:'.rjust(len_ljust, ' '), mana))
        print('%s %s' % ('Currency:'.rjust(len_ljust, ' '), currency))
        print('%s %s' % ('Perishables:'.rjust(len_ljust, ' '), perishables))
        print('%s %s' % ('Pet:'.rjust(len_ljust, ' '), nice_name(pet)))
        print('%s %s' % ('Mount:'.rjust(len_ljust, ' '), nice_name(mount)))
        print('%s %s' % ('Quest:'.rjust(len_ljust, ' '), quest))
        print('%s %s' % ('Party Health:'.rjust(len_ljust, ' '), member_health))

    # GET/POST habits (v3 ok)
    elif args['<command>'] == 'habits':
        habits = hbt.tasks.user(type='habits')
        direction = None
        if 'up' in args['<args>']:
            report = 'incremented'
            direction = 'up'
        elif 'down' in args['<args>']:
            report = 'decremented'
            direction = 'down'

        if direction != None:
            tids = get_task_ids(args['<args>'][1:])
            for tid in tids:
                tval = habits[tid]['value']
                habit = api.Habitica(auth=auth, resource="tasks", aspect=habits[tid]['id'])
                habit(_method='post', _one='score', _two=direction)
                print('%s habit \'%s\''
                      % (report, habits[tid]['text'].encode('utf8')))
                if direction == 'up':
                    habits[tid]['value'] = tval + (TASK_VALUE_BASE ** tval)
                else:
                    habits[tid]['value'] = tval - (TASK_VALUE_BASE ** tval)
                #sleep(HABITICA_REQUEST_WAIT_TIME)

        for i, task in enumerate(habits):
            score = qualitative_task_score_from_value(task['value'])
            print('[%s] %s %s' % (score, i + 1, task['text'].encode('utf8')))

    # GET/PUT tasks:daily (v3 ok)
    elif args['<command>'] == 'dailies':
        dailies = hbt.tasks.user(type='dailys')
        direction = None
        if 'done' in args['<args>']:
            report = 'completed'
            direction = 'up'
        elif 'undo' in args['<args>']:
            report = 'incomplete'
            direction = 'down'

        if direction != None:
            before_user = hbt.user()
            tids = get_task_ids(args['<args>'][1:])
            for tid in tids:
                daily = api.Habitica(auth=auth, resource="tasks", aspect=dailies[tid]['id'])
                daily(_method='post', _one='score', _two=direction)
                print('marked daily \'%s\' %s'
                      % (dailies[tid]['text'].encode('utf8'), report))
                if direction == 'up':
                    dailies[tid]['completed'] = True
                else:
                    dailies[tid]['completed'] = False
                #sleep(HABITICA_REQUEST_WAIT_TIME)
            show_delta(hbt, before_user, hbt.user())

        print_task_list(dailies)

    # handle todo items (v3 ok)
    elif args['<command>'] == 'todos':
        todos = [e for e in hbt.tasks.user(type='todos')
                 if not e['completed']]
        if 'done' in args['<args>']:
            before_user = hbt.user()
            tids = get_task_ids(args['<args>'][1:])
            for tid in tids:
                todo = api.Habitica(auth=auth, resource="tasks", aspect=todos[tid]['id'])
                todo(_method='post', _one='score', _two='up')
                print('marked todo \'%s\' complete'
                      % todos[tid]['text'].encode('utf8'))
                #sleep(HABITICA_REQUEST_WAIT_TIME)
            todos = updated_task_list(todos, tids)
            show_delta(hbt, before_user, hbt.user())
        elif 'get' in args['<args>']:
            tids = get_task_ids(args['<args>'][1:])
            for tid in tids:
                todo = api.Habitica(auth=auth, resource="tasks", aspect=todos[tid]['id'])
                obj = todo(_method='get')
                print(json.dumps({'todo':obj}, indent=4, sort_keys=True))
        elif 'add' in args['<args>']:
            ttext = ' '.join(args['<args>'][1:])
            hbt.tasks.user(type='todo',
                           text=ttext,
                           priority=PRIORITY[args['--difficulty']],
                           _method='post')
            todos.insert(0, {'completed': False, 'text': ttext})
            print('added new todo \'%s\'' % ttext.encode('utf8'))
        print_task_list(todos)

    else:
        print("Unknown command '%s'" % (args['<command>']))
        sys.exit(1)


if __name__ == '__main__':
    cli()
