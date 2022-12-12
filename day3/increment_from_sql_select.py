import json
import requests
import dlt


TWITTER_API_URL = "https://api.twitter.com/2/tweets/%s"


@dlt.source
def twitter_data(search_terms, last_value=None, twitter_bearer_token=dlt.secrets.value):
    return search_tweets(search_terms, last_value=last_value, twitter_bearer_token=twitter_bearer_token)


def _headers(twitter_bearer_token):
    """Constructs Bearer type authorization header as required by twitter api"""
    headers = {
        "Authorization": f"Bearer {twitter_bearer_token}"
    }
    return headers


def _paginated_get(url, headers, params, max_pages=5):
    """Requests and yields up to `max_pages` pages of results as per twitter api documentation: https://developer.twitter.com/en/docs/twitter-api/pagination"""
    while True:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        page = response.json()
        # show the pagination info
        meta = page["meta"]
        print(meta)

        yield page

        # get next page token
        next_token = meta.get('next_token')
        max_pages -= 1
        # if no more pages or we are at the maximum
        if not next_token or max_pages == 0:
            break
        else:
            # set the next_token parameter to get next page
            params['pagination_token'] = next_token



@dlt.resource(write_disposition="append")
def search_tweets(search_terms, last_value=None, twitter_bearer_token=dlt.secrets.value):
    headers = _headers(twitter_bearer_token)
    # get dlt state to store last values of tweets for each search term we request
    last_value_cache = dlt.state().setdefault("last_value_cache", {})

    # get search results for each term
    for search_term in search_terms:
        # use `last_value` to initialize state on the first run of the pipeline
        last_value = last_value_cache.setdefault(search_term, last_value or 0)
        print(f'last_value_cache: {last_value_cache[search_term]} for search term {search_term}')
        params = {
            'query': search_term,
            'max_results': 20,  # maximum elements per page: we set it to low value to demonstrate the paginator
            # basic twitter fields to be included in the data
            'tweet.fields': 'id,text,author_id,geo,created_at,lang,public_metrics,source',
            # uncomment below to include annotations and entities as described here: https://developer.twitter.com/en/docs/twitter-api/annotations/overview
            # 'tweet.fields': 'id,text,author_id,geo,created_at,lang,public_metrics,source,context_annotations,entities',
            # optional expansions with data related to returned tweets
            # 'expansions': 'author_id,geo.place_id',
            # 'user.fields': 'id,name,username,created_at,description,public_metrics,verified',
            # 'place.fields': 'full_name,id,country,country_code,geo,name,place_type'
        }

        # add last value to params only if it is set, see https://developer.twitter.com/en/docs/twitter-api/v1/tweets/timelines/guides/working-with-timelines
        if last_value > 0:
            params['since_id'] = last_value

        # get all the pages
        for page in _paginated_get(TWITTER_API_URL % "search/recent", headers, params):
            meta = page["meta"]
            # ignore pages without results
            if meta["result_count"] > 0:
                # store the last message id
                last_id = meta.get('newest_id', 0)
                last_value_cache[search_term] = max(last_value_cache[search_term], int(last_id))
                print(f'new_last_value_cache for term {search_term}: {last_value_cache[search_term]}')

                # add the search term that produced given page to the results
                page['search_term'] = search_term
                # remove the meta, we do not need to store it
                del page["meta"]
                # move all "expansions" to the page for nicer structure
                expansions = page.pop("includes", {})
                for k, v in expansions.items():
                    page[k] = v
                # yield the modified page to be loaded by dlt
                yield page


if __name__ == "__main__" :
    project_name = 'your_project_here'
    dataset_name ='tweets'
    search_terms = ['python data engineer job', "data engineering pipeline"]
    last_value_query = f'select max(id) from {dataset_name}.search_tweets'

    pipeline = dlt.pipeline(pipeline_name='twitter_day2_02', destination="bigquery", dataset_name=dataset_name)
    try:
        with pipeline.sql_client() as client:
            res = client.execute_sql(last_value_query)
            last_value = res[0][0]
    except:
        last_value = None

    # this implementation does not pass last value per search term, leading to issues if you use more than 1 search.
    # use the other pipeline that uses dlt-managed state for simpler management in code
    info = pipeline.run(twitter_data(search_terms=search_terms, last_value = last_value))

    # display where the data went
    #print(info)

    users_view_sql = f"""create or replace view `{project_name}.{dataset_name}.twitter_users` as
                        with numbered_rows as 
                          (SELECT 
                            u.*, 
                            row_number() over (partition by id order by page._dlt_load_id desc) as rownum
                          FROM `{project_name}.{dataset_name}.twitter_search__includes__users` as u
                          left join `{project_name}.{dataset_name}.twitter_search` as page
                            on page._dlt_id=u._dlt_parent_id)
                        select * 
                        from numbered_rows
                        where rownum=1
                        """

    with pipeline.sql_client() as client:
        client.execute_sql(users_view_sql)
        print('redeployed twitter_users view')
