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

# Fetch posts for a specific tag
FETCH_POSTS_QUERY = """
query GetPostsByTag($tagName: String!, $page: Int) {
  tag(name: $tagName) {
    postPager(type: NEW) {
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
