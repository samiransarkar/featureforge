# -*- coding: utf-8 -*-
import array
import logging

from future.builtins import map, range
import numpy
from schema import Schema, SchemaError, Use
from scipy.sparse import csr_matrix


logger = logging.getLogger(__name__)


class FeatureMappingFlattener(object):
    """
    This class maps feature tuples into numpy/scipy matrices.

    The main benefits of using it are:
        - String one-hot encoding is handled automatically
        - Input is validated so that each row preserves its "schema"
        - Generates sparse matrices

    A feature tuple is a regular python tuple of the shape:
        (
            ...
            3,         # Any int (or float)
            u"value",  # Any string (str or unicode)
            [1, 5, 9]  # A list of integers (or floats)
            ...
        )

    Tuple values are:
        - int/float
        - str/unicode: Are meant to be enumerated types and are one-hot
          encoded.
        - list/tuple/array of integers/floats: A convenience method to pack
          several numbers togheter but otherwise equivalent to inserting each
          value into the feature tuple.

    The flattener needs to be _fitted_ to the available feature tuples
    before being able to transform feature tuples to numpy/scipy matrices.
    This is because during fitting:
        - The dimension of the output matrix' rows are calculated.
        - A mapping between tuple indexes and output row indexes is fixed.
        - A schema of the data for validation is inferred.
        - One-hot encoding values are learned.
        - Validation is applied to the data being fitted.

    Validation checks:
        - Tuple size is always the same
        - Values' types comply with the above description.
        - The i-th value of the feature tuples doesn't have different types
          between different input tuples.

    After fitting the instance is ready to transform new feature tuples into
    numpy/scipy matrices as long as they comply with the schema inferred during
    fitting.
    """

    def __init__(self, sparse=True):
        """
        If `sparse` is `True` the transform/fit_transform methods generate a
        `scipy.sparse.csr_matrix` matrix.
        Else the transform/fit_transform generate `numpy.array` (dense).
        """
        self.sparse = sparse

    def fit(self, X, y=None):
        """Learns a mapping between feature tuples and matrix row indexes.

        Parameters
        ----------
        X : List, sequence or iterable of tuples but not a single tuple
        y : (ignored)

        Returns
        -------
        self
        """
        return self._wrapcall(self._fit, X)

    def transform(self, X, y=None):
        """Transform feature tuples to a numpy or sparse matrix.

        Parameters
        ----------
        X : List, sequence or iterable of tuples but not a single tuple
        y : (ignored)

        Returns
        -------
        Z : A numpy or sparse matrix
        """
        if self.sparse:
            return self._wrapcall(self._sparse_transform, X)
        else:
            return self._wrapcall(self._transform, X)

    def fit_transform(self, X, y=None):
        """Learns a mapping between feature tuples and matrix row indexes and
        then transforms the feature tuples to a numpy or sparse matrix.

        Parameters
        ----------
        X : List, sequence or iterable of tuples but not a single tuple
        y : (ignored)

        Returns
        -------
        Z : A numpy or sparse matrix
        """
        if self.sparse:
            return self._wrapcall(self._sparse_fit_transform, X)
        else:
            return self._wrapcall(self._fit_transform, X)

    def _wrapcall(self, method, X):
        try:
            return method(X)
        except SchemaError as e:
            raise ValueError(*e.args)

    def _add_column(self, i, value):
        key = (i, value)
        if key not in self.indexes:
            self.indexes[key] = len(self.indexes)
            self.reverse.append(key)

    def _fit_first(self, first):
        # Check for a tuples of numbers, strings or "sequences".
        schema = Schema((int, float, basestring, SequenceValidator()))
        schema.validate(first)
        if not first:
            raise ValueError("Cannot fit with no empty features")

        # Build validation schema using the first data point
        self.indexes = {}  # Tuple index to matrix column mapping
        self.reverse = []  # Matrix column to tuple index mapping
        self.schema = [None] * len(first)
        self.str_tuple_indexes = []
        for i, data in enumerate(first):
            if isinstance(data, (int, float)):
                type_ = Use(float)  # ints and floats are all mapped to float
                self._add_column(i, None)
            elif isinstance(data, basestring):
                type_ = basestring  # One-hot encoded indexes are added last
                self.str_tuple_indexes.append(i)
            else:
                type_ = SequenceValidator(data)
                for j in range(type_.size):
                    self._add_column(i, j)
            self.schema[i] = type_
        assert None not in self.schema
        self.schema = tuple(self.schema)
        self.validator = TupleValidator(self.schema)

    def _fit_step(self, datapoint):
        for i in self.str_tuple_indexes:
            self._add_column(i, datapoint[i])

    def _iter_valid(self, X, first=None):
        if first is not None:
            yield self.validator.validate(first)
        for datapoint in X:
            yield self.validator.validate(datapoint)

    def _fit(self, X):
        X = iter(X)
        try:
            first = next(X)
        except (TypeError, StopIteration):
            raise ValueError("Cannot fit with an empty dataset")
        logger.info("Starting flattener.fit")

        # Build basic schema
        self._fit_first(first)

        if self.str_tuple_indexes:  # Is there anything to one-hot encode ?
            # See all datapoints looking for one-hot encodeable feature values
            for datapoint in self._iter_valid(X, first=first):
                self._fit_step(datapoint)

        logger.info("Finished flattener.fit")
        logger.info("Input tuple size %s, output vector size %s" %
                     (len(first), len(self.indexes)))
        return self

    def _transform_step(self, datapoint):
        vector = numpy.zeros(len(self.indexes), dtype=float)
        for i, data in enumerate(datapoint):
            if isinstance(data, float):
                j = self.indexes[(i, None)]
                vector[j] = data
            elif isinstance(data, basestring):
                if (i, data) in self.indexes:
                    j = self.indexes[(i, data)]
                    vector[j] = 1.0
            else:
                j = self.indexes[(i, 0)]
                assert self.indexes[(i, len(data) - 1)] == \
                       j + len(data) - 1
                vector[j:j + len(data)] = data
        return vector

    def _transform(self, X):
        logger.info("Starting flattener.transform")
        matrix = []

        for datapoint in self._iter_valid(X):
            vector = self._transform_step(datapoint)
            matrix.append(vector.reshape((1, -1)))

        if not matrix:
            result = numpy.zeros((0, len(self.indexes)))
        else:
            result = numpy.concatenate(matrix)

        logger.info("Finished flattener.transform")
        logger.info("Matrix has size %sx%s" % result.shape)
        return result

    def _fit_transform(self, X):
        X = iter(X)
        try:
            first = next(X)
        except (TypeError, StopIteration):
            raise ValueError("Cannot fit with an empty dataset")
        logger.info("Starting flattener.fit_transform")

        self._fit_first(first)

        matrix = []
        for datapoint in self._iter_valid(X, first=first):
            self._fit_step(datapoint)
            vector = self._transform_step(datapoint)
            matrix.append(vector.reshape((1, -1)))

        N = len(self.indexes)
        for i, vector in enumerate(matrix):
            if len(vector) == N:
                break
            # This works because one-hot encoded features go at the end
            vector = numpy.array(vector)
            vector.resize((1, N))
            matrix[i] = vector

        if not matrix:
            result = numpy.zeros((0, N))
        else:
            result = numpy.concatenate(matrix)

        logger.info("Finished flattener.fit_transform")
        logger.info("Matrix has size %sx%s" % result.shape)
        return result

    def _sparse_transform_step(self, datapoint):
        for i, data in enumerate(datapoint):
            if isinstance(data, float):
                j = self.indexes[(i, None)]
                yield j, data
            elif isinstance(data, basestring):
                if (i, data) in self.indexes:
                    j = self.indexes[(i, data)]
                    yield j, 1.0
            else:
                j = self.indexes[(i, 0)]
                assert self.indexes[(i, len(data) - 1)] == \
                       j + len(data) - 1
                for k, data_k in enumerate(data):
                    yield j + k, data_k

    def _sparse_transform(self, X):
        logger.info("Starting flattener.transform")

        data = array.array("d")
        indices = array.array("i")
        indptr = array.array("i", [0])

        for datapoint in self._iter_valid(X):
            for i, value in self._sparse_transform_step(datapoint):
                if data != 0:
                    data.append(value)
                    indices.append(i)
            indptr.append(len(data))

        if not data:
            result = numpy.zeros((0, len(self.indexes)))
        else:
            result = csr_matrix((data, indices, indptr),
                                dtype=float,
                                shape=(len(indptr) - 1, len(self.indexes)))

        logger.info("Finished flattener.transform")
        logger.info("Matrix has size %sx%s" % result.shape)
        return result

    def _sparse_fit_transform(self, X):
        X = iter(X)
        try:
            first = next(X)
        except (TypeError, StopIteration):
            raise ValueError("Cannot fit with an empty dataset")
        logger.info("Starting flattener.fit_transform")

        self._fit_first(first)

        data = array.array("d")
        indices = array.array("i")
        indptr = array.array("i", [0])

        for datapoint in self._iter_valid(X, first=first):
            self._fit_step(datapoint)
            for i, value in self._sparse_transform_step(datapoint):
                if data != 0:
                    data.append(value)
                    indices.append(i)
            indptr.append(len(data))

        if not data:
            result = numpy.zeros((0, len(self.indexes)))
        else:
            result = csr_matrix((data, indices, indptr),
                                dtype=float,
                                shape=(len(indptr) - 1, len(self.indexes)))

        logger.info("Finished flattener.fit_transform")
        logger.info("Matrix has size %sx%s" % result.shape)
        return result


class SequenceValidator(object):
    def __init__(self, size=None):
        if size is None or isinstance(size, int):
            self.size = size
        else:
            seq = SequenceValidator().validate(size)
            self.size = len(seq)

    def validate(self, x):
        if not (isinstance(x, list) or isinstance(x, tuple) or
                isinstance(x, numpy.ndarray)):
            raise SchemaError("Sequence is not list, tuple or numpy array", [])
        if isinstance(x, numpy.ndarray):
            if x.dtype.kind != "f":
                raise SchemaError("Array dtype must be float, "
                                  "but was {}".format(x.dtype), [])
            x = x.ravel()
        if len(x) == 0:
            raise ValueError("Expecting a non-empty sequence but "
                             "got {}".format(x))
        if self.size is not None and len(x) != self.size:
            raise SchemaError("Expecting sequence length {} but got "
                              "{}".format(self.size, len(x)), [])
        if not isinstance(x, numpy.ndarray):
            for value in x:
                if not isinstance(value, (int, float)):
                    raise SchemaError("Values in sequence are expected to be "
                                      "numeric", [])
            x = numpy.array(x, dtype=float)
        return x

    def __str__(self):
        size = self.size
        if size is None:
            size = ""
        return "SequenceValidator({})".format(size)

    def __repr__(self):
        return str(self)


class TupleValidator(object):
    def __init__(self, types_tuple):
        self.tt = tuple(map(Schema, types_tuple))
        self.N = len(self.tt)

    def validate(self, x):
        if not isinstance(x, tuple):
            raise SchemaError("Expecting tuple, got {}".format(type(x)), [])
        if len(x) != self.N:
            raise SchemaError("Expecting a tuple of size {}, but got".format(
                              self.N, len(x)), [])
        return tuple(schema.validate(y) for y, schema in zip(x, self.tt))
