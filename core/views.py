import json
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.core.exceptions import ValidationError
from django.shortcuts import render
from django.core import serializers
from django.conf import settings
import datetime
import requests
import operator
from github import Github, GithubException
from .models import User, LastUpdate, Repository

AUTH_TOKEN = settings.GITHUB_AUTH_TOKEN
BASE_URL = settings.API_BASE_URL
ORG = settings.ORGANIZATION


def github():
    search_result = getOrganizationRepositories(ORG)
    if search_result and getOrganizationContributors(search_result):
        if LastUpdate.objects.filter(pk=1):
            LastUpdate.objects.filter(pk=1).update(
                updated=datetime.datetime.now())
        else:
            updated = LastUpdate(pk=1, updated=datetime.datetime.now())
            updated.save()
    return


def getOrganizationRepositories(org, url=''):
    repositories = []
    if not url:
        url = BASE_URL + 'orgs/%s/repos?per_page=100' % org
    response = requests.get(
        url, headers={'Authorization': 'token ' + AUTH_TOKEN})
    if (response.status_code == 200):  # 200 = SUCCESS
        repositories = response.json()
        if 'next' in response.links:
            repositories += getOrganizationRepositories(
                org, response.links['next']['url'])
    return repositories


def getOrganizationContributors(repoList):
    contributors = {}
    issueRepos = []
    for repo_ in repoList:
        currRepo = Repository.objects.filter(repo=repo_['name'])
        if not currRepo:
            newRepo = Repository(repo=repo_['name'], owner=repo_['owner']['login'], openIssues=repo_['open_issues'],
                                 include=True)
            newRepo.save()
        elif currRepo[0].openIssues != repo_['open_issues'] and currRepo[0].include:
            currRepo.update(openIssues=repo_['open_issues'])
            issueRepos += currRepo
        elif currRepo[0].include:
            issueRepos += currRepo

    forIssues = json.loads(serializers.serialize(
        'json', list(issueRepos), fields=('owner', 'repo')))
    for repo_ in forIssues:
        owner = repo_['fields']['owner']
        repoName = repo_['fields']['repo']
        contributors = getRepoIssues(owner, repoName, contributors)
        contributors = getRepoPR(owner, repoName, contributors)

    updateDataBase(contributors)
    return contributors != {}


def updateDataBase(contributors):
    for user in contributors:
        currUser = User.objects.filter(login=user)
        if currUser:
            currUser.update(
                totalOpenPRs=contributors[user]['open_prs'],
                totalMergedPRs=contributors[user]['merged_prs'],
                totalIssues=contributors[user]['issue_counts']
            )
        else:
            newUser = User(
                login=user,
                avatar=contributors[user]['avatar_url'],
                totalOpenPRs=contributors[user]['open_prs'],
                totalMergedPRs=contributors[user]['merged_prs'],
                totalIssues=contributors[user]['issue_counts']
            )
            newUser.save()


def getRepoPR(owner, repoName, contributors_, url=''):
    contributors = contributors_
    if not url:
        url = BASE_URL + \
            'repos/%s/%s/pulls?per_page=100&state=all' % (owner, repoName)
    response = requests.get(
        url, headers={"Authorization": "token " + AUTH_TOKEN})
    if (response.status_code == 200):  # 200 = SUCCESS
        pulls = response.json()
        savePRs(pulls, contributors)
        if 'next' in response.links:
            contributors = getRepoPR(
                owner, repoName, contributors, response.links['next']['url'])
    return contributors


def getRepoIssues(owner, repoName, contributors_, url=''):
    contributors = contributors_
    if not url:
        url = BASE_URL + \
            'repos/%s/%s/issues?per_page=100&state=all' % (owner, repoName)
    response = requests.get(
        url, headers={"Authorization": "token " + AUTH_TOKEN})
    if (response.status_code == 200):  # 200 = SUCCESS
        issues = response.json()

        saveIssues(issues, contributors)
        if 'next' in response.links:
            getRepoIssues(owner,
                          repoName,
                          contributors,
                          response.links['next']['url'])
    return contributors


def saveUser(userList, contributors):
    for user in userList:
        username = user['login'].lower()
        commits = user['contributions']
        if username in contributors:
            contributors[username]['merged_prs'] += commits
        else:
            contributors[username] = {}
            contributors[username]['merged_prs'] = commits
            contributors[username]['open_prs'] = 0
            contributors[username]['issue_counts'] = 0
            contributors[username]['avatar_url'] = user['avatar_url']
    return contributors


def savePRs(pullReq, contributors_):
    contributors = contributors_
    for pull in pullReq:
        if 'open' == pull['state']:
            username = pull['user']['login'].lower()
            if username in contributors:
                contributors[username]['open_prs'] += 1
            else:
                contributors[username] = {}
                contributors[username]['open_prs'] = 1
                contributors[username]['issue_counts'] = 0
                contributors[username]['merged_prs'] = 0
                contributors[username]['avatar_url'] = pull['user']['avatar_url']
        if 'closed' == pull['state'] and not pull['merged_at'] is None:
            username = pull['user']['login'].lower()
            if username in contributors:
                contributors[username]['merged_prs'] += 1
            else:
                contributors[username] = {}
                contributors[username]['open_prs'] = 0
                contributors[username]['issue_counts'] = 0
                contributors[username]['merged_prs'] = 1
                contributors[username]['avatar_url'] = pull['user']['avatar_url']

    return contributors


def saveIssues(issues, contributors_):
    contributors = contributors_
    for issue in issues:
        if not 'pull_request' in issue:
            username = issue['user']['login'].lower()
            if username in contributors:
                contributors[username]['issue_counts'] += 1
            else:
                contributors[username] = {}
                contributors[username]['issue_counts'] = 1
                contributors[username]['open_prs'] = 0
                contributors[username]['merged_prs'] = 0
                contributors[username]['avatar_url'] = issue['user']['avatar_url']

    return contributors


def showAll(request):
    sort = 'd'
    if 'sort' in request.GET:
        sort = request.GET['sort']
    if LastUpdate.objects.filter(pk=1):
        lastUpdated = LastUpdate.objects.get(pk=1).updated
    else:
        lastUpdated = ''
    users = sortUser(User.objects.all(), sort)

    data = serializers.serialize('json', list(users), fields=(
        'login', 'id', 'avatar', 'totalMergedPRs', 'gsoc', 'totalOpenPRs', 'totalIssues'))
    context = {
        'users': json.loads(data),
        'updated': lastUpdated,
    }
    return render(request, 'core/all_list.html', context)


def sortUser(_User, key, _gsoc=False):
    if key == 'm':
        return _User.order_by('-totalMergedPRs')
    if key == 'p':
        return _User.order_by('-totalOpenPRs')
    if key == 'i':
        return _User.order_by('-totalIssues')
    if _gsoc:  # defalut case for gsoc
        return User.objects.filter(gsoc=_gsoc).extra(
            select={'count': 'totalMergedPRs + totalOpenPRs'},
            order_by=('-count',),
        )
    else:  # default case for all
        return User.objects.extra(
            select={'count': 'totalMergedPRs + totalOpenPRs'},
            order_by=('-count',),
        )
