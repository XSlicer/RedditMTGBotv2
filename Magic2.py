#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import time
import praw
import re
import json
import signal
import sys
import pymysql as db
import warnings
from hashlib import md5
from requests import get
from config import *
from datetime import datetime
from os import path
from difflib import SequenceMatcher


# -----------------------------------------------------------------------
# OAuth init
oauth_timer = time.time() - 3600 # Subtract 3600 secs to force the first OAuth login
with open("oauth.txt", "r") as r:
        oauth_refkey = r.readlines()[0].strip()

# -----------------------------------------------------------------------
# Praw init
# This OAuth-data should be obtained from Reddit
# See https://github.com/reddit/reddit/wiki/OAuth2 or http://praw.readthedocs.io/en/stable/pages/oauth.html
# This is to ignore urllib3 ssl warnings
warnings.simplefilter("ignore", ResourceWarning)
botname = 'BOTNAME'
version = "v2.1"
owner = 'OWNER'
print("Starting {} {}".format(botname, version))
user_agent = ("Linux:{}:{} (by /u/{})".format(botname,version,owner))
r = praw.Reddit(user_agent=user_agent)
r.set_oauth_app_info(client_id=oa_clientid, client_secret=oa_secret,
                     redirect_uri='http://127.0.0.1:65010/authorize_callback')
subreddit = r.get_subreddit(subreddits)

# ----------------------------------------------------------------------
# Data init, Json files obtained from https://mtgjson.com/
def loaddata():
    print("Loading data...")
    global allcards, allsets, alllang, setnames
    with open(path.dirname(path.realpath(__file__)) + '/AllCards-x.json') as f:
        allcards = [i.lower() for i in list(json.load(f).keys())]
    with open(path.dirname(path.realpath(__file__))+ '/AllSets.json') as f:
        # Probably better to use AllCards-X and get the 'Printed in'-values. Rewrite the check.
        allsets = json.load(f)
    with open(path.dirname(path.realpath(__file__))+ '/AllSets-x.json') as f:
        # This should be done better
        alllang = []
        jd = json.load(f)
        for i in jd:
            for j in jd[i]['cards']:
                try:
                    for k in j['foreignNames']:
                        alllang.append(k['name'].lower())
                except KeyError:
                    pass
    setnames = {allsets[i]['name'].lower():i for i in allsets}
    print('Done!')
loaddata()

# ----------------------------------------------------------------------
# Core functions
class mysql:
    'Class for handling SQL inserts via a socket'
    def __init__(self):
        self.dbip = dbip
        self.dbuser = dbuser
        self.dbpass = dbpass
        self.dbname = dbname
        self.con = db.connect(self.dbip, self.dbuser, self.dbpass, self.dbname, charset='utf8mb4', connect_timeout=10)
        self.cur = self.con.cursor()
    def insert(self, q):
        self.cur.execute(q)
        self.con.commit()
    def select(self, q):
        self.cur.execute(q)
        return self.cur.fetchall()
    def close(self):
        self.con.close()
_sql = mysql()


def log(text):
    with open("bot.log", "a") as wl:
        time_ = str(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        wl.write('<' + time_ + '> ' + text + '\n')
        print(time_ + ': '+ text)


def debug(text):
    if DEBUG == True:
        time_ = str(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        print(time_ + ': ' + text)


def oauth_refresh(timer):
    global oauth_timer
    global r
    timediff = timer - oauth_timer
    if timediff > 3540:
        print("Refreshing oauth...", end='')
        oauth_timer = timer
        r.refresh_access_information(oauth_refkey)
        print('OK!')


# ----------------------------------------------------------------------
# Card name functions
def nicknames(name):
    # Hardcoded nicknames
    try:
        nicks = {"bob": "Dark Confidant",
                 "gary": "Gray Merchant of Asphodel",
                 "sad robot": "Solemn Simulacrum",
                 "jens": "Solemn Simulacrum",
                 "bolt": "Lightning Bolt",
                 "path": "Path to Exile",
                 "snappy": "Snapcaster Mage",
                 "tiago chan": "Snapcaster Mage",
                 "goyf": "Tarmogoyf",
                 "taylor swift": "Monastery Swiftspear",
                 "mom": "Mother of Runes",
                 "bfm": "B.F.M. (Big Furry Monster)",
                 "i can't even": "Void Winnower",
                 "durdle turtle": "Meandering Towershell",
                 "tim": "Prodigal Sorcerer",
                 "ernie": "Ernham Djinn",
                 "wog": "Wrath of God",
                 "finkel": "Shadowmage Infiltrator",
                 "jon finkel": "Shadowmage Infiltrator",
                 "titi": "Thing in the Ice",
                 "chris pikula": "Meddling Mage",
                 "superman": "Morphling",
                 "gitgud frog": "The Gitrog Monster"}
        return nicks[name.lower()]
    except KeyError:
        return False
def shortnames(name):
    # Function to get correct names out of stuff like 'Emrakul'
    regex = re.compile("^{}(([,])|( of )|( the )).*".format(name))
    realname = [m.group(0) for l in allcards for m in [regex.search(l)] if m]
    if realname:
        return realname[0]
    else:
        return False
def cardcheck(name):
    try:
        apinames = json.loads(get("http://mtgapi.samus.nl/cardjson/{}".format(name)).text)
    except: # Don't use except, but Requests' way of errors is a bit long
        return False
    score = 0
    for i in apinames:
        newscore = SequenceMatcher(None, name.lower(), i.lower()).ratio()
        if newscore > score:
            realname = i
            score = newscore
    try:
        return realname
    except UnboundLocalError:
        return False
def fullname(name):
    name = name.split('//')[0].strip()
    realname = False
    fetchtype = 'G'
    if name.lower() in allcards:
        realname = name
    if not realname:
        if name.lower() in alllang: realname = name
    if not realname:
        realname = nicknames(name)
    if not realname:
        realname = shortnames(name.lower())
    if not realname:
        if checkspoil(name, False):
            realname = name
    if not realname:
        if _sql.select("SELECT url FROM cards WHERE name ='{}';".format(name.replace("'", "''"))):
            realname = name
    if not realname:
        realname = cardcheck(name)
    return realname, fetchtype

# Information functions
def checkgather(name):
    # Checks if a card exists on the gatherer. The MD5-hash is the hash for the backside image.
    card = get("http://gatherer.wizards.com/Handlers/Image.ashx?name={}&type=card").text.encode('utf-8')
    hash = md5(card).hexdigest()
    return not hash == '6ae6826cde434d001e3b9522aa8c3b78'
def checkcard(name):
    # Check if a different URL for an image is in the database (extendable for high res etc)
    url = _sql.select("SELECT url FROM cards WHERE name ='{}';".format(name.replace("'", "''")))
    if not url:
        try:
            url = checkspoil(name, True)
        except IndexError:
            pass
    if url:
        return url[0][0]
    else:
        return "http://gatherer.wizards.com/Handlers/Image.ashx?name={}&type=card&.jpg".format(name)
def checkspoil(name ,geturl):
    if geturl:
        return _sql.select("SELECT url FROM spoilers WHERE name LIKE '{}';".format(name.lower().replace("'", "''").replace("_","\_")))
    else:
        return _sql.select("SELECT name FROM spoilers WHERE name LIKE '{}'".format(name.lower().replace("'", "''").replace("_","\_")))
def roborose(name):
    url = _sql.select("SELECT url FROM roborosewater WHERE name = '{}';".format(name.replace("'", "''")))
    if url:
        return url[0][0]
    else:
        return False

# ----------------------------------------------------------------------
# Reddit parsing
def getcomments():
    comments = subreddit.get_comments()
    debug("parsing comments...")
    for c in comments:
        if not str(c.author) in ["MTGCardFetcher"]:
            debug("doing SQL check")
            if not _sql.select("SELECT id FROM comments WHERE id = '{}';".format(c.id)): # Checks already parsed via SQL
                _sql.insert("INSERT INTO comments VALUES (NULL, '{}','{}',0,'{}','',NOW(),0,NULL)".format(c.id, c.subreddit, c.author))
                debug("doing process")
                text = process(c.body, c.id, 'comments', c.subreddit)
                if text:
                    _sql.insert("UPDATE comments SET postid='{}' WHERE id='{}';".format(c.submission.id, c.id))
                    try:
                        debug("posting")
                        text += "  \n^^^[[cardname]] ^^^or ^^^[[cardname|SET]] ^^^to ^^^call"
                        c.reply(text)
                        log('-- comment posted: {} --'.format(c.id))
                    except Exception as e:
                        log("ERROR: {}".format(str(e)))
                else:
#                    _sql.insert("DELETE FROM comments WHERE id='{}';".format(c.id))
                    pass

def getposts():
    selfposts = subreddit.get_new()
    debug("parsing posts...")
    for p in selfposts:
        debug("doing SQL check")
        if not _sql.select("SELECT id FROM posts WHERE id ='{}';".format(p.id)):
            _sql.insert("INSERT INTO posts VALUES (NULL, '{}','{}','{}','',NOW(),0,NULL)".format(p.id, p.subreddit, p.author))
            text = process(p.selftext, p.id, 'posts', p.subreddit)
            if text:
                try:
                    text += "  \n^^^[[cardname]] ^^^or ^^^[[cardname|SET]] ^^^to ^^^call"
                    p.add_comment(text)
                    log('-- reply posted: {} --'.format(p.id))
                except Exception as e:
                    log("ERROR: {}".format(str(e)))


def process(post, pid, posttype, postsub):
    quoteless = ''
    for i in post.split('\n'):
        if i and not i[0] == ">" and not i[0:4] == "&gt;":
            quoteless += i
    results = set(re.findall("\[\[([^\[\]]+)\]\]", quoteless))
    cards = [re.split('\||\\\\', i) for i in results]
    if len(cards) > 25: cards = cards[0:25]
    if cards:
        text = ''
        _sql.insert("UPDATE {} SET body='{}', fetched={} WHERE id='{}';".format(posttype, post.replace("'", "''"),len(cards), pid))
        if len(cards) > 3:
            text += "#####&#009;\n\n######&#009;\n\n####&#009;\n"
        for i in cards:
            if len(i) < 2:
                i.append('')
            if i[1].lower().strip() == 'rbrw':
                card = roborose(i[0])
                if card:
                    text += "  \n[{}]({})".format(i[0],card)
            else:
                realname, fetchtype = fullname(i[0])
                if realname and fetchtype == 'G':
                    m_id = 0
                    if len(i) > 1:
                        try:
                            i[1] = i[1].strip()
                            if i[1].lower() in setnames:
                                i[1] = setnames[i[1].lower()]
                            for j in allsets[i[1]]['cards']:
                                if j['name'].lower() == realname.lower():
                                    m_id = j['multiverseid']
                        except KeyError:
                            # Hier een extra check voor kaarten die niet in Gatherer zitten - MagicCards?
                            pass
                    if m_id:
                        text += "  \n[{}](http://gatherer.wizards.com/Handlers/Image.ashx?multiverseid={}&type=card&.jpg) - ".format(i[0], m_id)
                    else:
                        text += "  \n[{}]({}) - ".format(i[0].replace(')','\)'), checkcard(realname))
                    text += "[(G)](http://gatherer.wizards.com/Pages/Card/Details.aspx?name={})".format(realname)
                    text += " [(MC)](http://magiccards.info/query?q=!{})".format(realname)
                    text += " [(MW)](https://mtg.wtf/card?q=!{})".format(realname)
                    text += " [(CD)](http://combodeck.net/Card/{})".format(realname.replace(' ','_'))
                    if postsub in ["EDH","CompetitiveEDH"]:
                        text += " [(ER)](http://edhrec.com/cards/{})".format(realname)
        if len(text) > 38:
            return text
        else:
            return ''
    else:
        return False


# ----------------------------------------------------------------------
# App running
def signal_handler(signal, frame):
    # ctrl-c
    log("Shutting down...")
    _sql.close()
    sys.exit(0)
def reloaddata(signal, frame):
    log("Signal received - reloading latest MTGJson")
    loaddata()

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGCONT, reloaddata)

# main loop
while True:
    try:
        oauth_refresh(time.time())
    except praw.errors.HTTPException:
        log("OAuth Refresh failed, API down, retrying...")
        continue
    try:
        debug("Getting comments...")
        getcomments()
        debug("Getting posts...")
        getposts()
    except praw.errors.HTTPException:
        log("Reddit unreachable, retrying...")
    debug("sleep 5")
    time.sleep(5)
