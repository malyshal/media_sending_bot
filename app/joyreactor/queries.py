# GraphQL Queries for JoyReactor

# Search tags using the current working API schema
SEARCH_TAGS_QUERY = """
query SearchTags($mask: String!) {
  tagAutocomplete(mask: $mask) {
    id
    name
    count
    nsfw
    unsafe
  }
}
"""

# Fetch posts for a specific tag (NEW pager is ascending: page 1 = oldest!)
FETCH_POSTS_QUERY = """
query GetPostsByTag($tagName: String!, $page: Int) {
  tag(name: $tagName) {
    postPager(type: NEW) {
      count
      posts(page: $page) {
        id
        text
        createdAt
        nsfw
        unsafe
        postTags {
          tag {
            id
            name
          }
        }
        attributes {
          __typename
          ... on PostAttributePicture {
            id
            type
            image {
              id
              type
              hasVideo
              width
              height
            }
          }
        }
      }
    }
  }
}
"""

# Full-text search: finds posts by arbitrary query string (works even when the
# string is not a real tag) and can suggest similar tags (SEARCH section).
SEARCH_POSTS_QUERY = """
query SearchPosts($query: String!, $page: Int) {
  search(query: $query, sortByDate: true) {
    postPager {
      posts(page: $page) {
        id
        text
        createdAt
        nsfw
        unsafe
        postTags {
          tag {
            id
            name
          }
        }
        attributes {
          __typename
          ... on PostAttributePicture {
            id
            image {
              id
              hasVideo
              type
            }
          }
        }
      }
    }
  }
}
"""

# Site-like suggestions for arbitrary queries (like the website search box)
SEARCH_SIMILAR_QUERY = """
query SearchSimilar($query: String!) {
  search(query: $query) {
    tags {
      name
      count
    }
    similarQueries
  }
}
"""

# Fetch a single post by its global ID (API exposes node, not post)
GET_POST_QUERY = """
query GetPost($id: ID!) {
  node(id: $id) {
    ... on Post {
      id
      text
      createdAt
      nsfw
      unsafe
      postTags {
        tag {
          id
          name
        }
      }
      attributes {
        __typename
        ... on PostAttributePicture {
          id
          image {
            id
            hasVideo
            type
          }
        }
      }
    }
  }
}
"""

# Tag info lookup (canonical name check)
TAG_INFO_QUERY = """
query TagInfo($name: String!) {
  tag(name: $name) {
    id
    name
    seoName
    count
  }
}
"""
