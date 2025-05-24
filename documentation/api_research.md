# Shufersal's api url for all the results (Inside the returned json we should )
https://www.shufersal.co.il/online/he/search//results?q=

# TivTaam's api url for all the results
https://www.tivtaam.co.il/v2/retailers/1062/branches/924/products/autocomplete?appId=4&filters=%7B%22must%22:%7B%22exists%22:%5B%22family.id%22,%22family.categoriesPaths.id%22,%22branch.regularPrice%22%5D,%22term%22:%7B%22branch.isActive%22:true,%22branch.isVisible%22:true%7D%7D,%22mustNot%22:%7B%22term%22:%7B%22branch.regularPrice%22:0,%22branch.isOutOfStock%22:true%7D%7D%7D&from=0&isSearch=true&languageId=1&size=10

## Decoded url
```
https://www.tivtaam.co.il/v2/retailers/1062/branches/924/products/autocomplete?appId=4&filters={"must":{"exists":["family.id","family.categoriesPaths.id","branch.regularPrice"],"term":{"branch.isActive":true,"branch.isVisible":true}},"mustNot":{"term":{"branch.regularPrice":0,"branch.isOutOfStock":true}}}&from=0&isSearch=true&languageId=1&size=10
```

The tiv taam api is abit more complex. there are branches ids and categories ids. after that we'll have to find the products.

For example this is a request Boxed Salads category
`https://www.tivtaam.co.il/v2/retailers/1062/branches/924/categories/90191/products/filters?appId=4`
And the subcategory of the Boxed salads category is hummus/tahini
`https://www.tivtaam.co.il/v2/retailers/1062/branches/924/categories/90192/products/filters?appId=4`
With a different categories id
Looks like the retailers and branches ids stays the same (maybe specific tiv taam places?? but I'm surfing through the generic one...)


`https://www.tivtaam.co.il/v2/retailers/1062/branches/924/categories/90191/products?appId=4&categorySort=%7B%22sortType%22:2,%22topPriority%22:%22%5B%7B%5C%22id%5C%22:34961%7D,%7B%5C%22id%5C%22:39567%7D,%7B%5C%22id%5C%22:19748%7D,%7B%5C%22id%5C%22:42500%7D,%7B%5C%22id%5C%22:5637%7D,%7B%5C%22id%5C%22:35735%7D,%7B%5C%22id%5C%22:19721%7D,%7B%5C%22id%5C%22:57930%7D,%7B%5C%22id%5C%22:40945%7D%5D%22%7D&filters=%7B%22mustNot%22:%7B%22term%22:%7B%22branch.isOutOfStock%22:true%7D%7D%7D&from=0&languageId=1&minScore=0&names=%D7%A1%D7%9C%D7%98%D7%99%D7%9D+%D7%90%D7%A8%D7%95%D7%96%D7%99%D7%9D&names=Packaged+salads&names=%D0%A1%D0%B0%D0%BB%D0%B0%D1%82%D1%8B+%D0%B2+%D1%83%D0%BF%D0%B0%D0%BA%D0%BE%D0%B2%D0%BA%D0%B5&names=%D7%93%D7%9C%D7%99%D7%A7%D7%98%D7%A1%D7%99%D7%9D,+%D7%A0%D7%A7%D7%A0%D7%99%D7%A7%D7%99%D7%95%D7%AA+%D7%98%D7%A8%D7%99%D7%95%D7%AA+%D7%95%D7%92%D7%91%D7%99%D7%A0%D7%95%D7%AA&names=Deli+Cheese+%26+Sausage&names=%D0%94%D0%B5%D0%BB%D0%B8%D0%BA%D0%B0%D1%82%D0%B5%D1%81%D1%81%D1%8B,+%D0%BA%D0%BE%D0%BB%D0%B1%D0%B0%D1%81%D1%8B+%D0%B8+%D1%81%D1%8B%D1%80%D1%8B&size=12`



# TODOS
- [ ] Find the local barcode also, might be useful for searching or comparing?!
- [ ]