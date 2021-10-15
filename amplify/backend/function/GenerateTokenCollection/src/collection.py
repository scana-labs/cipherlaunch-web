from collections import defaultdict

from graphql import get_categories_under_project, get_traits_under_category, create_new_collection
from uuid import uuid4
from token_item import Token
import boto3
import json
import logging
import os
import random

s3_client = boto3.client("s3")

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

UTF_8_ENCODING = 'UTF-8'


class Collection:

    def __init__(self, project_id, project_name, base_url):
        self.project_id = project_id
        self.project_name = project_name
        self.base_url = base_url
        self.categories = self.get_project_categories()  # list of categories in order of ascending rank number
        self.category_trait_rarities, self.trait_images = self.get_metadata_maps()
        self.collection_id = str(uuid4())
        self.collection_bucket_name = "-".join(["tc", self.collection_id])

    def get_project_categories(self):
        return get_categories_under_project(self.project_id)

    def get_metadata_maps(self):
        categories_trait_rarities = {}
        trait_images = {}
        for category in self.categories:
            traits = []
            rarities = []
            category_traits = get_traits_under_category(category["category_id"])
            for trait in category_traits:
                traits.append(trait["name"])
                rarities.append(trait["rarity"])
                trait_images[trait["name"]] = trait["bucket_url"]
            categories_trait_rarities[category["name"]] = (traits, rarities)
        return categories_trait_rarities, trait_images

    def generate_tokens(self, total_tokens, token_id_offset=0):
        # Check that we have enough traits to generate total_tokens.
        total_possible = 1
        for category in self.category_trait_rarities:
            traits = self.category_trait_rarities[category]
            if traits:
                total_possible *= len(traits)
        if total_possible < total_tokens:
            raise Exception(f'Not enough traits to generate {total_tokens} tokens, only {total_possible} possible')
        elif total_possible * 0.8 <= total_tokens:
            print(f'Barely enough traits to generate {total_tokens} tokens, so may take a while')

        # Create necessary output folders.
        create_bucket_response \
            = s3_client.create_bucket(
            Bucket=self.collection_bucket_name,
            CreateBucketConfiguration={
                'LocationConstraint': os.getenv("REGION")
            }
        )
        if create_bucket_response['ResponseMetadata']['HTTPStatusCode'] != 200:
            status_code = create_bucket_response['ResponseMetadata']['HTTPStatusCode']
            logger.error(
                f"Received status code {status_code} when attempting to create bucket {self.collection_bucket_name}"
            )
            raise Exception(f"Failed to create bucket {self.collection_bucket_name}")

        logger.debug(f"Created S3 Bucket with name {self.collection_bucket_name}")

        # Trait generation.
        random.seed(42)
        tokens = []
        tokens_set = set()
        while len(tokens) < total_tokens:
            token = Token.random(self.category_trait_rarities)
            if token in tokens_set:  # TODO: incompatible traits
                continue
            tokens.append(token)
            tokens_set.add(token)

        # Add token ids to json.
        for i, token in enumerate(tokens):
            token.token_id = i + token_id_offset

        # Print all token traits.
        # print(list(items))

        # Get trait counts.
        trait_type_to_trait_counts_dict = defaultdict(lambda: defaultdict(int))
        for token in tokens:
            for trait_type, trait_value in token.token_traits.items():
                trait_type_to_trait_counts_dict[trait_type][trait_value] += 1

        token_trait_count_bytes = bytes(json.dumps(trait_type_to_trait_counts_dict).encode(UTF_8_ENCODING))
        key = f"trait-counts/trait-counts.json"
        trait_counts_upload_response = s3_client.put_object(
                Bucket=self.collection_bucket_name,
                Key=key,
                Body=token_trait_count_bytes
            )
        if trait_counts_upload_response['ResponseMetadata']['HTTPStatusCode'] != 200:
            status_code = create_bucket_response['ResponseMetadata']['HTTPStatusCode']
            logger.error(
                f"Received status code {status_code} when attempting to put object to bucket " +
                f"{self.collection_bucket_name} and key {key}"
            )
            raise Exception(f"Failed to upload trait counts to bucket {self.collection_bucket_name}")
        logger.debug(f"Uploaded trait counts to bucket {self.collection_bucket_name} and key {key}")

        # Write each item's metadata to a separate metadata file.
        for token in tokens:
            metadata_bytes = bytes(json.dumps(token.metadata(self.project_name, self.base_url)).encode(UTF_8_ENCODING))
            key = f"metadata/{token.token_id}.json"
            metadata_upload_response = s3_client.put_object(
                Bucket=self.collection_bucket_name,
                Key=key,
                Body=metadata_bytes
            )
            if metadata_upload_response['ResponseMetadata']['HTTPStatusCode'] != 200:
                status_code = create_bucket_response['ResponseMetadata']['HTTPStatusCode']
                logger.error(
                    f"Received status code {status_code} when attempting to put object to bucket " +
                    f"{self.collection_bucket_name} and key {key}"
                )
                raise Exception(f"Failed to upload metadata for token_id {token.token_id} to bucket " +
                                f"{self.collection_bucket_name}")
            logger.debug(f"Uploaded metadata for token_id {token.token_id} to {key}")

            bucket_url = f"s3://{self.collection_bucket_name}"

        created_collection = create_new_collection(self.collection_id, self.project_id, bucket_url)

        create_collection_id = created_collection["collection_id"]
        created_collection_bucket = created_collection["bucket_url"]

        logger.debug(
            f"Created collection with id {create_collection_id} and with bucket_url {created_collection_bucket}")

        return created_collection

