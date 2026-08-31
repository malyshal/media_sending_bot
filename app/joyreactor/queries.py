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
    postPager {
      posts(page: $page) {
        id
        text
        createdAt
        attributes {
          ... on PostAttributePicture {
            image {
              id
              type
              hasVideo
              width
              height
            }
          }
        }
        postTags {
          tag {
            name
          }
        }
      }
    }
  }
}
"""
