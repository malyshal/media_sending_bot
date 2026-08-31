# GraphQL Queries for JoyReactor

# Search tags using the current working API schema
# Based on gallery-dl: uses the tag page query to find related tags or specific autocomplete if available
# Since tagAutocomplete is deprecated, we use a query that returns tag info.
SEARCH_TAGS_QUERY = """
query SearchTags($mask: String!) {
  searchTags(mask: $mask) {
    id
    name
    count
    nsfw
    unsafe
  }
}
"""

# Fetch posts for a specific tag
# Updated to match actual API schema
FETCH_POSTS_QUERY = """
query GetPostsByTag($tagName: String!, $page: Int) {
  tag(name: $tagName) {
    postPager(type: NEW) {
      posts(page: $page) {
        id
        text
        createdAt
        attributes {
          ... on PostAttributePicture {
            image {
              id
              url
              type
            }
          }
          ... on PostAttributeVideo {
            video {
              id
              url
              type
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
