# GraphQL Queries for JoyReactor

FETCH_POSTS_QUERY = """
query FetchPosts($first: Int, $after: String, $tag: String) {
  posts(first: $first, after: $after, tag: $tag) {
    edges {
      node {
        id
        text
        createdAt
        updatedAt
        tags {
          name
        }
        attributes {
          id
          type
          image {
            url
          }
        }
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""

# Add other queries as needed
